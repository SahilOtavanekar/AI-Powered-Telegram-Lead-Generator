import os
import json
import modal

# --- MODAL CONFIGURATION ---
app = modal.App("telegram-lead-bot")

# Define the environment image with required dependencies
image = modal.Image.debian_slim().pip_install(
    "pyTelegramBotAPI==4.15.2",
    "google-generativeai>=0.8.0",
    "pydantic==2.6.3",
    "pyairtable==2.3.0",
    "requests",
    "beautifulsoup4", # Assuming scrape_google_maps uses these or similar
    "python-dotenv",
    "fastapi[standard]",
    "playwright"
).run_commands(
    "playwright install-deps",
    "playwright install chromium"
).add_local_dir(
    os.path.join(os.path.dirname(__file__)), remote_path="/root/src"
)

# --- BACKGROUND PROCESSOR ---
@app.function(
    image=image, 
    secrets=[modal.Secret.from_name("telegram-bot-secrets")],
    timeout=600 # Allow up to 10 minutes for scraping to complete
)
def process_message(request_body: dict):
    import sys
    sys.path.append("/root/src")
    
    import telebot
    import google.generativeai as genai
    from scrape_google_maps import scrape_google_maps
    from airtable_save_leads import airtable_save_leads
    from airtable_pull_leads import pull_airtable_leads
    
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    
    if not TELEGRAM_BOT_TOKEN or not GEMINI_API_KEY:
        print("Missing API Keys from Modal Secrets.")
        return

    bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
    genai.configure(api_key=GEMINI_API_KEY)

    try:
        update = telebot.types.Update.de_json(json.dumps(request_body))
        if not update.message or not update.message.text:
            return
            
        chat_id = update.message.chat.id
        user_text = update.message.text
        print(f"Processing TG Message: {user_text}")

        # Send an immediate acknowledgment for potentially long tasks
        bot.send_message(chat_id, "Thinking... 🧐")

        def scrape_and_save_leads(service: str, city: str, count: int) -> str:
            count = int(count)
            bot.send_message(chat_id, f"Navigating to Google Maps to scrape {count} {service} leads in {city}... This will take a moment 🌐")
            leads = scrape_google_maps(service, city, count)
            if not leads:
                return f"I couldn't find any {service} in {city} via Google Maps."

            # Build a preview from the scraped data
            preview = "\n\n**Top Scraped Leads:**\n"
            for i, lead in enumerate(leads[:3]):
                preview += f"{i+1}. {lead['name']} ({lead.get('rating', 'N/A')} stars) - {lead.get('address', 'Unknown Address')}\n"

            # Attempt to save — surface any Airtable errors directly to the user
            try:
                saved_count = airtable_save_leads(leads)
                if saved_count == 0:
                    return (
                        f"⚠️ Scraped {len(leads)} leads successfully, but saved 0 to Airtable.\n"
                        f"This usually means no records were created. Check Modal logs for details.{preview}"
                    )
                return f"✅ Successfully scraped {len(leads)} leads and saved {saved_count} to Airtable!{preview}"
            except Exception as airtable_err:
                error_msg = str(airtable_err)
                print(f"Airtable save error: {error_msg}")
                return (
                    f"⚠️ Scraped {len(leads)} leads, but failed to save to Airtable.\n\n"
                    f"**Error:** {error_msg}\n\n"
                    f"Common fixes:\n"
                    f"• Check that column names in Airtable exactly match: Name, service, address, website, rating, date_created, status\n"
                    f"• Ensure the token has `data.records:write` permission\n"
                    f"• Confirm the 'status' Single Select option is exactly 'Lead' (capital L)\n"
                    f"{preview}"
                )

        def search_existing_leads(city: str = None, service: str = None, min_rating: float = None, status: str = None, limit: int = 5) -> str:
            limit = int(limit) if limit else 5
            results = pull_airtable_leads(city=city, service=service, min_rating=min_rating, status=status, limit=limit)
            if not results: return "I couldn't find any existing leads in our database matching those criteria."
            preview = f"I found {len(results)} leads matching your criteria:\n\n"
            for i, lead in enumerate(results):
                preview += f"{i+1}. {lead.get('Name')} - {lead.get('service', 'N/A')} - {lead.get('rating', 'N/A')} stars\n"
            return preview

        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            tools=[scrape_and_save_leads, search_existing_leads],
            system_instruction=(
                "You are a professional, helpful assistant managing a lead generation system. "
                "When the user asks to find new leads or scrape data, call the scrape_and_save_leads tool. "
                "When the user asks to view existing leads, search the database, or pull data, call the search_existing_leads tool. "
                "Provide clear, conversational answers. Return only the user-facing text."
            )
        )

        chat = model.start_chat(enable_automatic_function_calling=True)
        response = chat.send_message(user_text)
        
        bot.send_message(chat_id, response.text)
        
    except Exception as e:
        import traceback
        print(f"Error processing webhook manually: {traceback.format_exc()}")
        try:
            bot.send_message(request_body.get('message', {}).get('chat', {}).get('id'), f"Oops! I encountered an error: {str(e)}")
        except:
            pass

# --- FAST WEBHOOK ENDPOINT ---
@app.function(image=image)
@modal.fastapi_endpoint(method="POST")
def webhook(request_body: dict):
    """
    This endpoint immediately accepts the Telegram request and spawns the background process.
    """
    process_message.spawn(request_body)
    return {"status": "ok"}

