from notion_client import Client
import requests
import base64
import os
import time
from dotenv import load_dotenv
load_dotenv()

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
ROOT_PAGE_ID = "Page id here"
OPEN_AI_ENDPOINT = os.environ["OPEN_AI_ENDPOINT"]
OPEN_AI_MODEL = "mycombo_gemini"
OPEN_AI_API_KEY = os.environ["OPEN_AI_API_KEY"]

notion = Client(auth=NOTION_TOKEN)


def get_child_pages(page_id):
    child_pages = []
    cursor = None
    while True:
        params = {"block_id": page_id}
        if cursor:
            params["start_cursor"] = cursor
        response = notion.blocks.children.list(**params)
        for block in response["results"]:
            if block["type"] == "child_page":
                child_pages.append({
                    "page_id": block["id"],
                    "title": block["child_page"]["title"]
                })
        if response["has_more"]:
            cursor = response["next_cursor"]
        else:
            break
    return child_pages


def get_image_blocks(page_id):
    image_blocks = []
    cursor = None
    while True:
        params = {"block_id": page_id}
        if cursor:
            params["start_cursor"] = cursor
        response = notion.blocks.children.list(**params)
        for block in response["results"]:
            if block["type"] == "image":
                img = block["image"]
                url = img["external"]["url"] if img["type"] == "external" else img["file"]["url"]
                caption = "".join([t["plain_text"] for t in img.get("caption", [])])
                image_blocks.append({
                    "block_id": block["id"],
                    "url": url,
                    "current_caption": caption
                })

        if response["has_more"]:
            cursor = response["next_cursor"]
        else:
            break
    return image_blocks


def download_image(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.google.com/",
        "Connection": "keep-alive",
    }

    print("Downloading image...", url)
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "image/png")
    mime_type = content_type.split(";")[0].strip()
    return response.content, mime_type


def ocr_with_openai(image_bytes, mime_type):
    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{mime_type};base64,{b64_image}"

    payload = {
        "model": OPEN_AI_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url}
                    },
                    {
                        "type": "text",
                        "text": (
                            "I need the information from image to search later."
                            "Summarize the main points from the image in up to 5 sentences. "
                            "Only provide the summary; do not use phrases like 'The image includes.' "
                            "Provide the summary followed by about 10 main English keywords separated by commas, each keyword is bound in '', prioritize keywords with two or more words. "
                            "If there is no text in the image, return: (none)."
                        )
                    }
                ]
            }
        ],
        "max_tokens": 4096,
        "stream": False,
        "thinking": False
    }

    response = requests.post(
        f"{OPEN_AI_ENDPOINT}/chat/completions",
        headers={
            "Authorization": f"Bearer {OPEN_AI_API_KEY}",
            "Content-Type": "application/json"
        },
        json=payload,
        timeout=30
    )
    response.raise_for_status()
    print(response.text)
    result = response.json()
    print("Raw result = ",result)
    return result["choices"][0]["message"]["content"].strip()


def update_caption(block_id, text):
    notion.blocks.update(
        block_id=block_id,
        **{
            "image": {
                "caption": [{"type": "text", "text": {"content": text}}]
            }
        }
    )


def mark_page_as_done(page_id, current_title):
    new_title = f"[*] {current_title}"
    notion.pages.update(
        page_id=page_id,
        properties={
            "title": {
                "title": [{"type": "text", "text": {"content": new_title}}]
            }
        }
    )
    print(f"   🏷 Đã đổi title thành: {new_title}")


def run():
    print(f"🔍 Đang lấy danh sách child pages từ: {ROOT_PAGE_ID}\n")
    pages = get_child_pages(ROOT_PAGE_ID)

    if not pages:
        print("⚠ Không tìm thấy child page nào.")
        return

    print(f"📚 Tìm thấy {len(pages)} pages:\n")
    for p in pages:
        print(f"  • {p['title']} ({p['page_id']})")
    print()

    for page in pages:
        if page["title"].startswith("[*]"):
            print(f"\n⏭ Bỏ qua (đã xử lý): {page['title']}")
            continue

        print(f"\n📄 Page: {page['title']}")
        print(f"   ID: {page['page_id']}")

        image_blocks = get_image_blocks(page["page_id"])

        if not image_blocks:
            print(f"   ⚠ Không có ảnh nào")
            mark_page_as_done(page["page_id"], page["title"])
            continue

        print(f"   🖼 Tìm thấy {len(image_blocks)} ảnh")

        for i, block in enumerate(image_blocks, 1):
            print(f"\n   [{i}/{len(image_blocks)}] Ảnh {block['block_id'][:8]}...")

            # ✅ Kiểm tra caption: nếu đã có nội dung thì bỏ qua
            if block["current_caption"].strip():
                print(f"   ⏭ Caption đã có sẵn, bỏ qua: {block['current_caption'][:80]}")
                continue

            image_bytes, mime_type = download_image(block["url"])
            print(mime_type)

            max_retries = 1
            for attempt in range(1, max_retries + 1):
                try:
                    ocr_text = ocr_with_openai(image_bytes, mime_type)

                    if "(none)" in ocr_text:
                        print(f"   ⚠ Không đọc được chữ, bỏ qua")
                        break

                    # ✅ Ghép tiền tố [AI] vào đầu caption
                    final_caption = f"[*] {ocr_text}"

                    update_caption(block["block_id"], final_caption)
                    print(f"   ✅ Caption: {final_caption[:80]}")
                    break

                except Exception as e:
                    print(f"   ❌ Lỗi lần {attempt}: {e}")
                    if attempt < max_retries:
                        print("   ⏳ Đợi 15 giây rồi thử lại...")
                        time.sleep(15)
                    else:
                        print("   🚫 Đã thử tối đa, bỏ qua ảnh này")

        mark_page_as_done(page["page_id"], page["title"])

    print("\n\n✅ Hoàn tất!")


if __name__ == "__main__":
    run()
