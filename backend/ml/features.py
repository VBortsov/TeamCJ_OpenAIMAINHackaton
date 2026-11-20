"""Feature builder shared by training and live scoring. Only columns from the scraper contract are used.
Contract inputs: country_code, is_germany, city, is_university_city, profile2026_description, profile2026_tags,
profile2026_is_deeptech, profile2026_mentions_ai, n_founders_listed / fd_n_founders_found, has_cto_founder,
fd_team_has_technical_founder, fd_university_spinoff, fd_any_* (NaN -> 0, no missingness indicators).
NOT used: accelerator*, cohort_year (only a training control, fixed at scoring), outcome_*, label_*, service columns.
"""
import numpy as np
import pandas as pd

HUBS = {"berlin": "berlin", "munich": "munich", "münchen": "munich", "garching": "munich", "paris": "paris",
        "amsterdam": "amsterdam", "copenhagen": "copenhagen", "zurich": "zurich", "zürich": "zurich", "hamburg": "hamburg"}
COUNTRIES = ["DEU", "FRA", "NLD", "DNK", "CHE", "AUT"]
TAGS = ["fintech", "b2b", "saas", "healthcare", "climate", "artificial intelligence", "hardware", "consumer",
        "marketplace", "biotech", "robotics", "developer tools", "energy", "education", "proptech", "logistics"]
FD = ["fd_any_phd", "fd_any_top_university", "fd_any_stem_degree", "fd_any_bigtech", "fd_any_academic_researcher",
      "fd_any_serial_founder", "fd_any_prior_exit", "fd_any_top_consulting", "fd_any_elite_business_school",
      "fd_any_studied_abroad", "fd_team_has_technical_founder", "fd_university_spinoff"]
ACCELERATORS = ["yc", "techstars", "antler", "ef", "sosv", "500", "alchemist"]
# compact feature set that won the contract-compliant search (within-year AUC on label_progressed)
SMALL = ["ctrl_cohort_year", "is_university_city", "n_founders", "solo_founder", "has_cto_founder", "tag_fintech", "tag_b2b",
         "tag_saas", "is_deeptech", "mentions_ai", "is_germany", "country_FRA"] + FD
REF_YEAR = 2021  # scoring-time value of the cohort-year control (same for every live lead -> does not change their order)


def col(df, name, default=np.nan):
    return df[name] if name in df else pd.Series(default, index=df.index)


def build_features(df: pd.DataFrame, cohort_year=None) -> pd.DataFrame:
    X = pd.DataFrame(index=df.index)
    cc = col(df, "country_code", "").fillna("").astype(str).str.upper()
    for c in COUNTRIES:
        X[f"country_{c}"] = (cc == c).astype(int)
    X["is_germany"] = pd.to_numeric(col(df, "is_germany", 0), errors="coerce").fillna((cc == "DEU").astype(int))
    city = col(df, "city", "").fillna("").astype(str).str.lower().str.strip()
    for h in sorted(set(HUBS.values())):
        X[f"hub_{h}"] = city.map(HUBS).eq(h).astype(int)
    X["is_university_city"] = pd.to_numeric(col(df, "is_university_city", 0), errors="coerce").fillna(0)
    tags = (col(df, "profile2026_tags", "").fillna("").astype(str) + " " + col(df, "profile2026_description", "").fillna("").astype(str)).str.lower()
    for t in TAGS:
        X["tag_" + t.replace(" ", "_")] = tags.str.contains(t, regex=False).astype(int)
    X["is_deeptech"] = pd.to_numeric(col(df, "profile2026_is_deeptech", 0), errors="coerce").fillna(0)
    X["mentions_ai"] = pd.to_numeric(col(df, "profile2026_mentions_ai", 0), errors="coerce").fillna(0)
    # 0 founders means "none found yet" (the scraper exports 0, training has NaN) -> unknown, not a team of zero
    nfl = pd.to_numeric(col(df, "n_founders_listed"), errors="coerce").where(lambda s: s > 0)
    nff = pd.to_numeric(col(df, "fd_n_founders_found"), errors="coerce").where(lambda s: s > 0)
    nf = nfl.fillna(nff)
    X["n_founders"] = nf.fillna(2).clip(1, 6)
    X["solo_founder"] = (nf == 1).astype(int)
    X["has_cto_founder"] = pd.to_numeric(col(df, "has_cto_founder", 0), errors="coerce").fillna(0)
    for c in FD:
        X[c] = (pd.to_numeric(col(df, c), errors="coerce") == 1).astype(int)  # NaN -> 0
    X["fd_n_positive_signals"] = X[FD].sum(axis=1)
    X["ctrl_cohort_year"] = REF_YEAR if cohort_year is None else cohort_year
    acc = col(df, "accelerator", "").fillna("").astype(str).str.lower().str.strip()
    acc = acc.replace({"y combinator": "yc", "entrepreneur first": "ef", "500 global": "500", "hax": "sosv", "indiebio": "sosv"})
    for a in ACCELERATORS:
        X[f"acc_{a}"] = (acc == a).astype(int)
    X["acc_other_program"] = ((acc != "") & ~acc.isin(ACCELERATORS)).astype(int)
    return X.astype(float)


def has_accelerator(df: pd.DataFrame) -> pd.Series:
    return col(df, "accelerator", "").fillna("").astype(str).str.strip().ne("")
