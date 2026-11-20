"""
Configuration & Settings Management for MAIN Summit 2026 HTGF Sourcing Engine.
"""

from pathlib import Path
from typing import Set
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Paid / Hackathon Tokens
    OPENAI_API_KEY: str | None = Field(default=None, description="OpenAI API key for Wave 2/3 LLM tasks")
    OPENAI_MEMO_MODEL: str = Field(default="gpt-6.2-sol", description="OpenAI model for the ML memo agent (backend/ml/memo_agent.py)")
    ELEVENLABS_API_KEY: str | None = Field(default=None, description="ElevenLabs API key for Wave 3 partner briefing audio")
    N8N_WEBHOOK_URL: str | None = Field(default=None, description="Webhook for Top-10 leads dispatch")

    # Optional Free / Community API Keys
    OPENALEX_API_KEY: str | None = Field(default=None, description="OpenAlex API key for higher rate limits")
    HF_TOKEN: str | None = Field(default=None, description="Hugging Face User Access Token")
    GITHUB_TOKEN: str | None = Field(default=None, description="GitHub Personal Access Token")
    OPENREGISTER_API_KEY: str | None = Field(default=None, description="OpenRegister API key")

    # Storage & Cache Configuration
    DATA_DIR: Path = Field(default=Path("./data"))
    SQLITE_DB_PATH: Path = Field(default=Path("./data/scout.db"))
    HTTP_CACHE_DIR: Path = Field(default=Path("./data/http_cache"))
    HTTP_CACHE_ENABLED: bool = Field(default=True)
    HTTP_CACHE_TTL_HOURS: int = Field(default=24)

    # Scraping & Concurrency Limits
    MAX_CONCURRENT_REQUESTS: int = Field(default=5)
    REQUEST_TIMEOUT_SECONDS: float = Field(default=30.0)
    USER_AGENT: str = Field(
        default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    )


# DACH & Top European Research Labs / Universities (OpenAlex Institution Lineage IDs)
EU_RESEARCH_INSTITUTES = {
    "ETH Zurich": "I35440088",
    "Technical University of Munich (TUM)": "I62916508",
    "RWTH Aachen University": "I887968799",
    "TU Delft": "I98358874",
    "Max Planck Society": "I149899117",
    "Fraunhofer-Gesellschaft": "I4923324",
    "EPFL (Ecole Polytechnique Federale de Lausanne)": "I165779595",
    "Karlsruhe Institute of Technology (KIT)": "I4210086259",
    "University of Cambridge": "I241749",
    "University of Oxford": "I40120149",
    "INRIA": "I4210137021",
}

# IDs pipe-separated for OpenAlex filter syntax
OPENALEX_PRIMARY_LAB_IDS = [
    "I35440088",   # ETH Zurich
    "I62916508",   # TUM
    "I887968799",  # RWTH Aachen
    "I98358874",   # TU Delft
    "I149899117",  # Max Planck
    "I4923324",    # Fraunhofer
]

# Platform & aggregator domains that must NEVER be treated as an entity's custom domain
PLATFORM_DOMAINS: Set[str] = {
    "huggingface.co", "github.com", "gitlab.com", "lu.ma", "luma.com",
    "linkedin.com", "twitter.com", "x.com", "medium.com", "substack.com",
    "youtube.com", "arxiv.org", "orcid.org", "openalex.org", "doi.org",
    "wikipedia.org", "notion.site", "linktr.ee", "google.com", "apple.com"
}

# Established Big Tech, Legacy Corporates & Research Institutes Blocklist
BIG_TECH_BLOCKLIST: Set[str] = {
    # US / Global Big Tech & Legacy Hardware/IT
    "google", "alphabet", "deepmind", "meta", "facebook", "microsoft", "nvidia",
    "apple", "amazon", "aws", "intel", "amd", "ibm", "oracle", "salesforce",
    "adobe", "cisco", "qualcomm", "broadcom", "uber", "netflix", "paypal",
    "hewlett packard", "hpe", "hp", "seagate", "western digital", "dell",
    # Asian Giants & Conglomerates
    "samsung", "huawei", "alibaba", "tencent", "baidu", "bytedance", "tsmc",
    "sony", "panasonic", "hitachi", "nec", "fujitsu", "lenovo", "sumitomo",
    # German & European Conglomerates & Industrial Legacy
    "sap", "siemens", "bosch", "robert bosch", "bmw", "mercedes", "mercedes-benz",
    "daimler", "volkswagen", "vw", "porsche", "audi", "basf", "bayer",
    "boehringer ingelheim", "merck", "sanofi", "novartis", "roche", "airbus",
    "philips", "asml", "infineon", "deutsche telekom", "telekom", "ericsson",
    "nokia", "zf friedrichshafen", "continental", "thyssenkrupp", "e.on", "rwe",
    "zeiss", "carl zeiss", "trumpf", "dow", "dow chemical", "knauf", "nxp",
    "regeneron", "akzonobel", "framatome", "arup", "cosmote", "rai",
    "valeo", "safran", "thales", "alstom", "stmicroelectronics", "henkel",
    # Research Institutes / Consortia (not startups)
    "fraunhofer", "max planck", "helmholtz", "leibniz", "flanders make",
    "eurecat", "silicon austria labs", "vito", "tno", "vtt", "inria", "cern",
    # Well-known Late Stage / Public European Tech
    "spotify", "adyen", "klarna", "delivery hero", "zalando", "auto1", "hellofresh",
    # Open Source Foundations / Consortiums
    "apache", "linux foundation", "eclipse foundation", "w3c"
}

settings = Settings()
