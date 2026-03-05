import os
import sys
import json
import telebot
import google.generativeai as genai
from dotenv import load_dotenv
from pyairtable import Api

# Import our custom execution functions
from scrape_google_maps import scrape_google_maps
from airtable_save_leads import airtable_save_leads
from airtable_pull_leads import pull_airtable_leads

# Load environment variables
load_dotenv()

# Check for required API keys
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_BOT_TOKEN or not GEMINI_API_KEY:
    print("Error: Missing TELEGRAM_BOT_TOKEN or GEMINI_API_KEY in environment.")
    print("Please follow instructions in `instructions/api_keys_setup.md`.")
    sys.exit(1)

# Initialize Telegram Bot
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# Initialize Gemini
genai.configure(api_key=GEMINI_API_KEY)

# Define tools for Gemini
def scrape_and_save_leads(service: str, city: str, count: int) -> str:
    """
    Scrape Google Maps for business leads matching the service and city, and save them to Airtable.
    
    Args:
        service: The type of business (e.g., "plumber", "coffee shop").
        city: The city to search in (e.g., "Miami").
        count: The number of leads to scrape and save.
        
    Returns:
        A formatted string summarizing the operation.
    """
    print(f"Executing Scrape Tool: {count} {service} in {city}")
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
                f"Check your terminal logs for details.{preview}"
            )
        return f"✅ Successfully scraped {len(leads)} leads and saved {saved_count} to Airtable!{preview}"
    except Exception as airtable_err:
        error_msg = str(airtable_err)
        print(f"Airtable save error: {error_msg}")
        return (
            f"⚠️ Scraped {len(leads)} leads, but failed to save to Airtable.\n\n"
            f"**Error:** {error_msg}\n\n"
            f"Common fixes:\n"
            f"• Check column names in Airtable exactly match: Name, service, address, website, rating, date_created, status\n"
            f"• Ensure the token has `data.records:write` permission\n"
            f"• Confirm the 'status' Single Select option is exactly 'Lead' (capital L)\n"
            f"{preview}"
        )

def search_existing_leads(city: str = None, service: str = None, min_rating: float = None, status: str = None, limit: int = 5) -> str:
    """
    Search our existing Airtable database for leads matching specific filters.
    
    Args:
        city: Optional. Filter by city location.
        service: Optional. Filter by type of service (e.g., "plumber").
        min_rating: Optional. Minimum rating value.
        status: Optional. Current lead status.
        limit: Max number of leads to return. Default is 5.
        
    Returns:
        A formatted string with the found results.
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

# Setup the Gemini Model
generation_config = genai.types.GenerationConfig(
    temperature=0.3, # Keep it relatively deterministic to favor tool calls
)

# Using gemini-2.5-flash which supports function calling natively
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

# Start a chat session globally to maintain light context if needed
# Note: For production with multiple users, you'd track chat sessions by chat_id.
chat_sessions = {}

def get_chat_session(chat_id):
    if chat_id not in chat_sessions:
        chat_sessions[chat_id] = model.start_chat(enable_automatic_function_calling=True)
    return chat_sessions[chat_id]

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    chat_id = message.chat.id
    user_text = message.text
    
    print(f"\n[Telegram] Received message from {message.from_user.first_name}: {user_text}")
    
    # Send a typing indicator to Telegram so the user knows we're thinking
    bot.send_chat_action(chat_id, 'typing')
    
    try:
        # Get or create the Gemini session for this user
        chat = get_chat_session(chat_id)
        
        # Send message to Gemini. Automatic function calling will intercept,
        # run the tools locally, and then ask Gemini to generate the final response.
        response = chat.send_message(user_text)
        
        final_answer = response.text
        bot.reply_to(message, final_answer)
        
    except Exception as e:
        print(f"Error handling message: {e}")
        bot.reply_to(message, "Sorry, I ran into an error trying to process your request.")

if __name__ == "__main__":
    print("Starting Telegram Bot with Gemini intent routing...")
    print("Polling for messages...")
    try:
        bot.polling(none_stop=True)
    except KeyboardInterrupt:
        print("\nStopping bot...")
        sys.exit(0)
