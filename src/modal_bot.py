import os
import json
import modal

# --- MODAL CONFIGURATION ---
app = modal.App("telegram-lead-bot")

# Define the environment image with required dependencies
image = modal.Image.debian_slim().pip_install(
    "pyTelegramBotAPI==4.15.2",
    "openai>=1.12.0",
    "pydantic==2.6.3",
    "pyairtable==2.3.0",
    "requests",
    "beautifulsoup4",
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
    from openai import OpenAI
    from scrape_google_maps import scrape_google_maps
    from airtable_save_leads import airtable_save_leads
    from airtable_pull_leads import pull_airtable_leads
    
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
    
    if not TELEGRAM_BOT_TOKEN or not OPENAI_API_KEY:
        print("Missing API Keys from Modal Secrets.")
        return

    bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
    client = OpenAI(api_key=OPENAI_API_KEY)

    try:
        update = telebot.types.Update.de_json(json.dumps(request_body))
        if not update.message or not update.message.text:
            return
            
        chat_id = update.message.chat.id
        user_text = update.message.text
        print(f"Processing TG Message: {user_text}")

        # Send an immediate acknowledgment
        bot.send_message(chat_id, "Thinking... 🧐")

        def scrape_and_save_leads(service: str, city: str, count: int) -> str:
            count = int(count)
            bot.send_message(chat_id, f"Navigating to Google Maps to scrape {count} {service} leads in {city}... 🌐")
            leads = scrape_google_maps(service, city, count)
            if not leads:
                return f"I couldn't find any {service} in {city} via Google Maps."

            preview = "\n\n**Top Scraped Leads:**\n"
            for i, lead in enumerate(leads[:3]):
                preview += f"{i+1}. {lead['name']} ({lead.get('rating', 'N/A')} stars) - {lead.get('address', 'Unknown Address')}\n"

            try:
                saved_count = airtable_save_leads(leads)
                if saved_count == 0:
                    return f"⚠️ Scraped {len(leads)} leads successfully, but saved 0 to Airtable.{preview}"
                return f"✅ Successfully scraped {len(leads)} leads and saved {saved_count} to Airtable!{preview}"
            except Exception as e:
                return f"⚠️ Scraped {len(leads)} leads, but failed to save to Airtable.\n\nError: {str(e)}{preview}"

        def search_existing_leads(city: str = None, service: str = None, min_rating: float = None, status: str = None, limit: int = 5) -> str:
            limit = int(limit) if limit else 5
            results = pull_airtable_leads(city=city, service=service, min_rating=min_rating, status=status, limit=limit)
            if not results: return "I couldn't find any existing leads in our database."
            preview = f"I found {len(results)} leads matching your criteria:\n\n"
            for i, lead in enumerate(results):
                preview += f"{i+1}. {lead.get('Name')} - {lead.get('service', 'N/A')} - {lead.get('rating', 'N/A')} stars\n"
            return preview

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "scrape_and_save_leads",
                    "description": "Scrape Google Maps for business leads and save them to Airtable.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "service": {"type": "string"},
                            "city": {"type": "string"},
                            "count": {"type": "integer"}
                        },
                        "required": ["service", "city", "count"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "search_existing_leads",
                    "description": "Search our existing Airtable database for leads.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string"},
                            "service": {"type": "string"},
                            "min_rating": {"type": "number"},
                            "status": {"type": "string"},
                            "limit": {"type": "integer"}
                        }
                    }
                }
            }
        ]

        messages = [
            {"role": "system", "content": "You are a lead generation assistant. Help users gather and manage leads using the provided tools."},
            {"role": "user", "content": user_text}
        ]

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )

        response_message = response.choices[0].message
        tool_calls = response_message.tool_calls

        if tool_calls:
            messages.append(response_message)
            for tool_call in tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)
                
                if function_name == "scrape_and_save_leads":
                    result = scrape_and_save_leads(**function_args)
                elif function_name == "search_existing_leads":
                    result = search_existing_leads(**function_args)
                else:
                    result = "Error."

                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": result
                })
            
            second_response = client.chat.completions.create(
                model="gpt-4o",
                messages=messages
            )
            final_answer = second_response.choices[0].message.content
        else:
            final_answer = response_message.content
        
        bot.send_message(chat_id, final_answer)
        
    except Exception as e:
        import traceback
        print(f"Error: {traceback.format_exc()}")
        try:
            bot.send_message(chat_id, f"Oops! I encountered an error: {str(e)}")
        except: pass

# --- FAST WEBHOOK ENDPOINT ---
@app.function(image=image)
@modal.fastapi_endpoint(method="POST")
def webhook(request_body: dict):
    process_message.spawn(request_body)
    return {"status": "ok"}


