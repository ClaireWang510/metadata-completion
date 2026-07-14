"""Global config: models, thresholds, budgets."""
import os
from pathlib import Path

from dotenv import load_dotenv


# Load the project-level .env before reading any configuration values.
# Existing process environment variables take precedence (override=False).
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=False)

# ---- LLM ----
PLANNER_MODEL   = os.getenv("PLANNER_MODEL",   "gpt-4o-mini")
VERIFIER_MODEL  = os.getenv("VERIFIER_MODEL",  "gpt-4o-mini")
LINK_MODEL      = os.getenv("LINK_MODEL",      "gpt-4o-mini")
VLM_MODEL       = os.getenv("VLM_MODEL",       "gpt-4o-mini")   # for pdf agent (multimodal)

# ---- thresholds / budget ----
CONF_THRESHOLD  = float(os.getenv("CONF_THRESHOLD", "0.75"))
MAX_ITERATIONS  = int(os.getenv("MAX_ITERATIONS", "3"))
MAX_TOTAL_TOKENS_PER_PAPER = int(os.getenv("MAX_TOTAL_TOKENS_PER_PAPER", "60000"))

# ---- APIs ----
OPENALEX_BASE   = "https://api.openalex.org"
OPENALEX_MAILTO = os.getenv("OPENALEX_MAILTO", "")
DBLP_BASE       = "https://dblp.org/search/publ/api"
CROSSREF_BASE   = "https://api.crossref.org/works"
IEEE_BASE       = "https://ieeexploreapi.ieee.org/api/v1/articles/doi"
IEEE_API_KEY    = os.getenv("IEEE_API_KEY", "")

HTTP_TIMEOUT    = 20.0
HTTP_RETRIES    = 3

# ---- feature switches ----
ENABLE_PDF_AGENT = os.getenv("ENABLE_PDF_AGENT", "0") == "1"   # off by default (省 token)
