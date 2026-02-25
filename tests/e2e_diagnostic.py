"""Check what tables exist in the public schema on Supabase."""
import os, sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supabase import create_client
url = os.environ["SUPABASE_URL"]
key = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(url, key)

# Try to query raw_recipes
try:
    r = sb.table("raw_recipes").select("*").limit(1).execute()
    print("raw_recipes: EXISTS", len(r.data or []), "rows")
except Exception as e:
    print("raw_recipes: MISSING -", str(e)[:150])

# Try raw_ingredients
try:
    r = sb.table("raw_ingredients").select("*").limit(1).execute()
    print("raw_ingredients: EXISTS", len(r.data or []), "rows")
except Exception as e:
    print("raw_ingredients: MISSING -", str(e)[:150])
