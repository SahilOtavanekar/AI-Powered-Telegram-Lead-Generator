import os
from typing import List, Dict, Any
from dotenv import load_dotenv
from pyairtable import Api

# Load environment variables
load_dotenv()

def airtable_save_leads(leads: List[Dict[str, Any]]) -> int:
    """
    Saves a list of leads to Airtable using the pyairtable SDK.
    Raises an exception on failure so the caller can surface the error to the user.
    """
    api_key = os.getenv("AIRTABLE_API_KEY")
    base_id = os.getenv("AIRTABLE_BASE_ID")
    table_name = os.getenv("AIRTABLE_TABLE_NAME")

    if not all([api_key, base_id, table_name]):
        raise EnvironmentError(
            "Airtable configuration is incomplete. "
            "Ensure AIRTABLE_API_KEY, AIRTABLE_BASE_ID, and AIRTABLE_TABLE_NAME are set."
        )

    api = Api(api_key)
    table = api.table(base_id, table_name)

    print(f"Attempting to upload {len(leads)} leads to Airtable Base '{base_id}', Table '{table_name}'...")

    # Map local scraped keys → exact Airtable column names (case-sensitive!)
    mapped_leads = []
    for lead in leads:
        raw_rating = lead.get("rating")
        parsed_rating = None
        if raw_rating and raw_rating != "N/A":
            try:
                parsed_rating = float(raw_rating)
            except (ValueError, TypeError):
                pass

        record = {
            "Name": lead.get("name"),
            "service": lead.get("service"),
            "address": lead.get("address"),
            "website": lead.get("website") or "",
            "rating": parsed_rating,
            # date_created: send date portion only (YYYY-MM-DD)
            "date_created": lead.get("date_created", "").split(" ")[0] if lead.get("date_created") else None,
            # NOTE: 'status' is intentionally omitted here.
            # Airtable Single Select fields require the option to already exist AND
            # the token needs schema.bases:write permission to create new options.
            # If you want to set status on create, first ensure "Lead" exists in the
            # Airtable Single Select options, then uncomment the line below:
            # "status": "Lead",
        }
        mapped_leads.append(record)

    # Filter out None values so Airtable doesn't choke on null fields for required columns
    cleaned_leads = [
        {k: v for k, v in record.items() if v is not None and v != ""}
        for record in mapped_leads
    ]

    print(f"Fields being pushed per record: {list(cleaned_leads[0].keys()) if cleaned_leads else '(none)'}")

    # batch_create handles chunking into groups of 10 automatically
    created_records = table.batch_create(cleaned_leads)

    count = len(created_records)
    print(f"Successfully uploaded {count} leads to Airtable.")
    return count


if __name__ == "__main__":
    test_leads = [
        {
            "name": "Test Coffee Shop",
            "service": "coffee shop",
            "address": "123 Test St, Test City",
            "website": "https://test.com",
            "rating": "4.5",
            "date_created": "2026-03-05 15:00:00",
            "status": "lead",
        }
    ]

    print("Testing Airtable save workflow...")
    try:
        result = airtable_save_leads(test_leads)
        print(f"✅ Total leads saved: {result}")
    except Exception as e:
        print(f"❌ Save failed: {e}")
