import os
import sys
import json
import telebot
from openai import OpenAI
from dotenv import load_dotenv

# Import our custom execution functions
from scrape_google_maps import scrape_google_maps
from airtable_save_leads import airtable_save_leads
from airtable_pull_leads import pull_airtable_leads

# Load environment variables
load_dotenv()

# Check for required API keys
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TELEGRAM_BOT_TOKEN or not OPENAI_API_KEY:
    print("Error: Missing TELEGRAM_BOT_TOKEN or OPENAI_API_KEY in environment.")
    sys.exit(1)

# Initialize Telegram Bot
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# Initialize OpenAI Client
client = OpenAI(api_key=OPENAI_API_KEY)

# --- Tool Definitions for OpenAI ---

def scrape_and_save_leads(service: str, city: str, count: int) -> str:
    """
    Scrape Google Maps for business leads matching the service and city, and save them to Airtable.
    """
    print(f"Executing Scrape Tool: {count} {service} in {city}")
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
    except Exception as airtable_err:
        return f"⚠️ Scraped {len(leads)} leads, but failed to save to Airtable.\n\nError: {str(airtable_err)}{preview}"

def search_existing_leads(city: str = None, service: str = None, min_rating: float = None, status: str = None, limit: int = 5) -> str:
    """
    Search our existing Airtable database for leads matching specific filters.
    """
    print(f"Executing Search Tool: city={city}, service={service}, rating={min_rating}, limit={limit}")
    results = pull_airtable_leads(city=city, service=service, min_rating=min_rating, status=status, limit=limit)
    
    if not results:
        return "I couldn't find any existing leads in our database matching those criteria."
        
    preview = f"I found {len(results)} leads matching your criteria (sorted by rating):\n\n"
    for i, lead in enumerate(results):
        preview += f"{i+1}. {lead['Name']} - {lead.get('service', 'N/A')} - {lead.get('rating', 'N/A')} stars\n"
        preview += f"   Location: {lead.get('address', 'N/A')}\n"
        preview += f"   Status: {lead.get('status', 'N/A')}\n\n"
    return preview

# Define tools schema for OpenAI
tools = [
    {
        "type": "function",
        "function": {
            "name": "scrape_and_save_leads",
            "description": "Scrape Google Maps for business leads matching the service and city, and save them to Airtable.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "The type of business (e.g., 'plumber', 'coffee shop')."},
                    "city": {"type": "string", "description": "The city to search in (e.g., 'Miami')."},
                    "count": {"type": "integer", "description": "The number of leads to scrape and save."}
                },
                "required": ["service", "city", "count"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_existing_leads",
            "description": "Search our existing Airtable database for leads matching specific filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "Optional city filter."},
                    "service": {"type": "string", "description": "Optional service filter."},
                    "min_rating": {"type": "number", "description": "Minimum rating value."},
                    "status": {"type": "string", "description": "Current lead status."},
                    "limit": {"type": "integer", "description": "Max number of leads to return (default 5)."}
                }
            }
        }
    }
]

# Track conversation history per user
chat_histories = {}

def get_history(chat_id):
    if chat_id not in chat_histories:
        chat_histories[chat_id] = [
            {"role": "system", "content": (
                "You are a professional lead generation assistant. Help users gather and manage leads.\n\n"
                "GUIDELINES:\n"
                "1. SCRAPE NEW DATA: If the user asks to 'find', 'get', 'scrape', or 'search' for leads, use `scrape_and_save_leads`.\n"
                "2. SEARCH DATABASE: If the user mentions 'database', 'existing', or 'already saved', use `search_existing_leads`.\n"
                "3. If search returns nothing, suggest scraping instead."
            )}
        ]
    return chat_histories[chat_id]

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    chat_id = message.chat.id
    user_text = message.text
    print(f"\n[Telegram] Received: {user_text}")
    
    bot.send_chat_action(chat_id, 'typing')
    history = get_history(chat_id)
    history.append({"role": "user", "content": user_text})
    
    try:
        # Step 1: Send the conversation and available tools to the model
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=history,
            tools=tools,
            tool_choice="auto"
        )
        
        response_message = response.choices[0].message
        tool_calls = response_message.tool_calls
        
        # Step 2: Check if the model wanted to call a tool
        if tool_calls:
            history.append(response_message)
            
            for tool_call in tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)
                
                # Routing to actual Python functions
                if function_name == "scrape_and_save_leads":
                    result = scrape_and_save_leads(**function_args)
                elif function_name == "search_existing_leads":
                    result = search_existing_leads(**function_args)
                else:
                    result = "Error: Tool not found."
                
                history.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": result
                })
            
            # Get a final response from the model after tool execution
            second_response = client.chat.completions.create(
                model="gpt-4o",
                messages=history
            )
            final_answer = second_response.choices[0].message.content
        else:
            final_answer = response_message.content
            
        history.append({"role": "assistant", "content": final_answer})
        bot.reply_to(message, final_answer)
        
    except Exception as e:
        print(f"Error: {e}")
        bot.reply_to(message, "Sorry, I ran into an error trying to process your request.")

if __name__ == "__main__":
    print("Starting Telegram Bot with OpenAI...")
    # Clear any existing webhook to avoid '409 Conflict' during local polling
    bot.remove_webhook()
    bot.polling(none_stop=True)

