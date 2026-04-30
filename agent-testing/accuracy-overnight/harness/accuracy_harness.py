#!/usr/bin/env python3
"""Local harness for the WorldFork overnight accuracy evaluation.

The harness intentionally writes only under agent-testing/accuracy-overnight.
It keeps prompt dossiers anonymized and keeps real sources/outcomes in the
separate sources directory.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import textwrap
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
STUDY = ROOT / "agent-testing" / "accuracy-overnight"
EVENTS_DIR = STUDY / "events"
SOURCES_DIR = STUDY / "sources"
RUNS_DIR = STUDY / "runs"
ANALYSIS_DIR = STUDY / "analysis"
LATEX_DIR = STUDY / "latex"
LOG_PATH = STUDY / "REPRODUCIBILITY_LOG.md"
GEMINI = "google/gemini-3.1-flash-lite-preview"
DEFAULT_BASE_URL = "http://127.0.0.1:18013"

CATEGORIES = [
    "tech/platform",
    "AI/public systems",
    "labor/social movements",
    "elections/legitimacy",
    "public health",
    "environment/resource",
    "corporate PR crisis",
    "policy/regulatory backlash",
    "campus/civil society",
    "finance/market confidence",
]

HORIZONS = ["weeks", "1-3 months", "3-12 months", "1-3 years", "3+ years"]


def src(title: str, url: str, kind: str = "reference") -> dict[str, str]:
    return {"title": title, "url": url, "kind": kind}


def ev(
    event_id: str,
    category: str,
    horizon: str,
    case: str,
    start: str,
    situation: str,
    stakeholders: list[str],
    pressures: list[str],
    constraints: list[str],
    uncertainties: list[str],
    actual: str,
    distribution: dict[str, float],
    sources: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "id": event_id,
        "category": category,
        "horizon": horizon,
        "real_case": case,
        "start": start,
        "situation": situation,
        "stakeholders": stakeholders,
        "pressures": pressures,
        "constraints": constraints,
        "uncertainties": uncertainties,
        "actual_outcome": actual,
        "expected_distribution": distribution,
        "sources": sources,
    }


EVENTS: list[dict[str, Any]] = [
    ev(
        "tech_weeks_api_pricing_blackout",
        "tech/platform",
        "weeks",
        "Reddit API protests, 2023",
        "A large online community platform announces a sudden paid access regime for a developer interface used by third-party clients and volunteer moderation tools.",
        "Volunteer moderators coordinate a short service blackout while platform executives signal that monetization and control of external clients are non-negotiable.",
        ["platform executives", "volunteer moderators", "third-party developers", "heavy users", "advertisers"],
        ["loss of moderator trust", "developer exit threats", "advertiser sensitivity", "platform dependence by communities"],
        ["the platform owns the infrastructure", "moderators have symbolic leverage but limited formal authority", "users have switching costs"],
        ["whether the blackout lasts beyond the first week", "whether administrators replace moderators", "whether accessibility or moderation exceptions are granted"],
        "policy_implemented_with_mod_concessions",
        {"policy_implemented_with_mod_concessions": 0.62, "major_platform_reversal": 0.12, "durable_user_exodus": 0.18, "negotiated_delay": 0.08},
        [
            src("Reddit API controversy", "https://en.wikipedia.org/wiki/Reddit_API_controversy"),
            src("CNBC: Reddit in crisis as moderators protest API price increase", "https://www.cnbc.com/2023/06/16/reddit-in-crisis-as-prominent-moderators-protest-api-price-increase.html"),
            src("Guardian: How social media's biggest user protest rocked Reddit", "https://www.theguardian.com/technology/2023/dec/30/reddit-moderator-protest-communities-social-media"),
        ],
    ),
    ev(
        "tech_1_3m_privacy_policy_backlash",
        "tech/platform",
        "1-3 months",
        "WhatsApp privacy policy backlash, 2021",
        "A dominant encrypted messaging service tells users to accept revised data-sharing terms with its parent company or lose access after a deadline.",
        "Confusion about privacy changes drives public backlash and migration talk, but the service has enormous network effects.",
        ["messaging service operator", "parent platform", "privacy advocates", "ordinary users", "rival apps"],
        ["viral misinformation", "network lock-in", "regulatory scrutiny", "deadline pressure"],
        ["users coordinate mainly through the same service", "rivals can absorb some migration but not whole social graphs", "the operator can delay without abandoning policy"],
        ["whether trust loss causes permanent exit", "whether regulators intervene", "whether clarified messaging reduces panic"],
        "delayed_clarified_policy_with_limited_exit",
        {"delayed_clarified_policy_with_limited_exit": 0.58, "full_policy_withdrawal": 0.16, "large_durable_migration": 0.18, "regulatory_pause": 0.08},
        [
            src("WhatsApp privacy policy change", "https://en.wikipedia.org/wiki/WhatsApp#2021_privacy_policy_change"),
            src("WhatsApp help: 2021 update", "https://faq.whatsapp.com/595164741332628"),
            src("BBC: WhatsApp delays privacy policy change", "https://www.bbc.com/news/technology-55684419"),
        ],
    ),
    ev(
        "tech_3_12m_platform_deplatforming",
        "tech/platform",
        "3-12 months",
        "Parler deplatforming after January 2021 violence",
        "A social app popular with a politically mobilized audience is removed from major app stores and hosting infrastructure after allegations that violent organizing was tolerated.",
        "The app loses mainstream distribution and hosting at once, while supporters seek alternative infrastructure and critics demand stronger moderation.",
        ["app leadership", "cloud host", "mobile app stores", "political users", "safety advocates"],
        ["infrastructure dependency", "free-speech framing", "safety liability", "alternative hosting scramble"],
        ["major platforms control distribution chokepoints", "migration requires technical rebuild", "public attention is intense but may decay"],
        ["whether alternative infrastructure can relaunch quickly", "whether app stores re-admit the service", "whether users return after downtime"],
        "service_relaunches_with_reduced_reach",
        {"service_relaunches_with_reduced_reach": 0.52, "permanent_shutdown": 0.18, "full_mainstream_restoration": 0.15, "fragmented_successor_networks": 0.15},
        [
            src("Parler", "https://en.wikipedia.org/wiki/Parler"),
            src("Reuters: Amazon to drop Parler from hosting", "https://www.reuters.com/article/us-apple-parler/amazon-to-drop-parler-from-its-web-hosting-service-says-report-idUSKBN29E0J9"),
            src("NPR: Parler partially reappears online", "https://www.npr.org/2021/02/15/967990656/parler-partially-reappears-online-with-support-from-russian-owned-technology-firm"),
        ],
    ),
    ev(
        "tech_1_3y_social_platform_buyout",
        "tech/platform",
        "1-3 years",
        "Elon Musk acquisition of Twitter, 2022",
        "A high-profile buyer signs a binding deal to acquire a major social platform, then attempts to exit the deal while financing, courts, employees, advertisers, and users react.",
        "The platform faces ownership uncertainty, legal deadlines, advertiser concerns, and employee morale collapse before a forced endpoint.",
        ["buyer", "board", "court", "employees", "advertisers", "power users"],
        ["specific performance litigation", "debt financing", "brand safety fears", "product governance uncertainty"],
        ["the merger contract is enforceable", "the buyer controls public narratives", "advertisers can pause spending faster than users can migrate"],
        ["whether the deal closes", "whether the buyer renegotiates price", "whether post-close governance destabilizes revenue"],
        "deal_closes_followed_by_restructuring_and_brand_turbulence",
        {"deal_closes_followed_by_restructuring_and_brand_turbulence": 0.62, "renegotiated_lower_price": 0.16, "deal_collapse": 0.12, "smooth_private_turnaround": 0.10},
        [
            src("Acquisition of Twitter by Elon Musk", "https://en.wikipedia.org/wiki/Acquisition_of_Twitter_by_Elon_Musk"),
            src("SEC exhibit: merger agreement announcement", "https://www.sec.gov/Archives/edgar/data/1418091/000119312522120461/d310843dex991.htm"),
            src("Reuters: Musk closes Twitter deal", "https://www.reuters.com/markets/deals/elon-musk-closes-44-bln-twitter-deal-sources-2022-10-28/"),
        ],
    ),
    ev(
        "tech_3y_filesharing_injunction",
        "tech/platform",
        "3+ years",
        "Napster litigation and shutdown",
        "A fast-growing peer-to-peer media-sharing service becomes culturally ubiquitous while rights holders sue over mass copyright infringement.",
        "The service has explosive user adoption but depends on a legally vulnerable centralized index and has little time to become licensed.",
        ["rights holders", "startup operators", "users", "courts", "potential acquirers"],
        ["network effects", "injunction risk", "music industry bargaining power", "technical workarounds"],
        ["copyright law gives plaintiffs strong remedies", "users can migrate to successor tools", "licensed conversion requires capital and rights deals"],
        ["whether the service can settle", "whether an injunction kills the core product", "whether the brand survives through acquisition"],
        "injunction_shutdown_bankruptcy_licensed_brand_survives",
        {"injunction_shutdown_bankruptcy_licensed_brand_survives": 0.66, "licensed_conversion": 0.14, "technical_evasion_success": 0.12, "industry_loses_case": 0.08},
        [
            src("A&M Records v. Napster", "https://en.wikipedia.org/wiki/A%26M_Records,_Inc._v._Napster,_Inc."),
            src("EFF case archive: A&M Records v. Napster", "https://www.eff.org/cases/am-records-v-napster"),
            src("CNET: Napster files for bankruptcy", "https://www.cnet.com/tech/services-and-software/napster-files-for-bankruptcy/"),
        ],
    ),
    ev(
        "ai_weeks_exam_algorithm_reversal",
        "AI/public systems",
        "weeks",
        "UK school exam grading algorithm controversy, 2020",
        "A national exam authority replaces canceled exams with an algorithmic grade standardization process using school history and teacher estimates.",
        "Students receive downgraded results, protests escalate, universities need admissions decisions, and ministers must choose between consistency and legitimacy.",
        ["education ministry", "exam regulator", "students", "schools", "universities"],
        ["perceived class bias", "deadline pressure", "admissions bottlenecks", "public protest"],
        ["exams cannot be re-run quickly", "teacher estimates are available", "government can override regulator policy"],
        ["whether leaders defend statistical standardization", "whether appeals can absorb anger", "whether teacher grades become the fallback"],
        "algorithm_scrapped_teacher_assessments_used",
        {"algorithm_scrapped_teacher_assessments_used": 0.70, "appeals_process_only": 0.12, "partial_hybrid_revision": 0.14, "algorithm_upheld": 0.04},
        [
            src("2020 UK school exam grading controversy", "https://en.wikipedia.org/wiki/2020_United_Kingdom_school_exam_grading_controversy"),
            src("Ofqual summer 2020 grading documents", "https://www.gov.uk/government/publications/awarding-gcse-as-a-level-advanced-extension-awards-and-extended-project-qualifications-in-summer-2020"),
            src("BBC: A-level and GCSE results U-turn", "https://www.bbc.com/news/uk-53810655"),
        ],
    ),
    ev(
        "ai_1_3m_hospital_vaccine_algorithm",
        "AI/public systems",
        "1-3 months",
        "Stanford hospital vaccine allocation algorithm, 2020",
        "A hospital uses an allocation algorithm to prioritize scarce vaccine appointments and frontline trainees discover they were mostly excluded.",
        "Residents protest because algorithmic criteria over-weight seniority and location while under-weighting bedside exposure.",
        ["hospital leadership", "frontline trainees", "senior physicians", "patients", "public observers"],
        ["scarce doses", "moral legitimacy", "exposure risk", "reputational pressure"],
        ["allocation can be revised quickly", "frontline staff have public sympathy", "the hospital can apologize without external legislation"],
        ["whether leaders defend the model", "whether manual review replaces ranking", "whether trust recovers"],
        "apology_and_algorithm_revision",
        {"apology_and_algorithm_revision": 0.72, "algorithm_defended": 0.08, "external_investigation": 0.10, "ad_hoc_manual_distribution": 0.10},
        [
            src("MIT Technology Review: Stanford vaccine algorithm", "https://www.technologyreview.com/2020/12/21/1015303/stanford-vaccine-algorithm/"),
            src("NPR: Stanford apologizes after allocation algorithm", "https://www.npr.org/sections/health-shots/2020/12/18/948043622/stanford-apologizes-after-vaccine-allocation-algorithm-leaves-out-frontline-docto"),
            src("The Verge: Stanford vaccine algorithm", "https://www.theverge.com/2020/12/18/22189291/stanford-hospital-vaccine-algorithm-medical-residents"),
        ],
    ),
    ev(
        "ai_3_12m_welfare_risk_system_ban",
        "AI/public systems",
        "3-12 months",
        "Dutch SyRI welfare fraud risk system court ruling, 2020",
        "A government risk-scoring system combines citizen data across agencies to identify suspected welfare fraud in poorer neighborhoods.",
        "Civil society groups challenge the system as opaque, discriminatory, and incompatible with rights protections.",
        ["welfare ministry", "municipal agencies", "civil rights groups", "residents", "court"],
        ["privacy rights", "poverty stigma", "fraud-control pressure", "algorithmic opacity"],
        ["the system depends on legal authority", "affected citizens lack individual transparency", "courts can halt deployment"],
        ["whether the government proves proportionality", "whether the system is paused or banned", "whether similar tools continue under new labels"],
        "court_bans_system_for_rights_violation",
        {"court_bans_system_for_rights_violation": 0.64, "system_continues_with_safeguards": 0.18, "temporary_pause_only": 0.12, "government_prevails": 0.06},
        [
            src("Systeem Risico Indicatie", "https://en.wikipedia.org/wiki/Systeem_Risico_Indicatie"),
            src("Dutch court press release on SyRI", "https://www.rechtspraak.nl/Organisatie-en-contact/Organisatie/Rechtbanken/Rechtbank-Den-Haag/Nieuws/Paginas/Dutch-Court-prohibits-the-use-of-SyRI.aspx"),
            src("Privacy International: Dutch court rules SyRI violates rights", "https://privacyinternational.org/news-analysis/3363/dutch-court-rules-welfare-surveillance-system-syri-violates-human-rights"),
        ],
    ),
    ev(
        "ai_1_3y_debt_recovery_scandal",
        "AI/public systems",
        "1-3 years",
        "Australia Robodebt scheme collapse",
        "A welfare agency automates debt notices using income averaging, producing large numbers of disputed debts against benefit recipients.",
        "Legal aid groups, affected citizens, journalists, and courts pressure the government to admit the method is unlawful.",
        ["welfare agency", "ministers", "benefit recipients", "courts", "public auditors"],
        ["budget-recovery incentives", "citizen hardship", "legal uncertainty", "administrative denial"],
        ["many debts are already collected", "the agency controls records", "courts can force settlement", "political accountability may lag"],
        ["whether debts are paused", "whether compensation is paid", "whether a public inquiry follows"],
        "program_ended_refunds_settlement_and_inquiry",
        {"program_ended_refunds_settlement_and_inquiry": 0.66, "minor_process_fix": 0.10, "court_loss_without_inquiry": 0.14, "policy_defended": 0.10},
        [
            src("Robodebt scheme", "https://en.wikipedia.org/wiki/Robodebt_scheme"),
            src("Robodebt Royal Commission", "https://robodebt.royalcommission.gov.au/"),
            src("Parliamentary Library: Robodebt", "https://www.aph.gov.au/About_Parliament/Parliamentary_departments/Parliamentary_Library/pubs/rp/BudgetReview202021/Robodebt"),
        ],
    ),
    ev(
        "ai_3y_childcare_benefit_scandal",
        "AI/public systems",
        "3+ years",
        "Dutch childcare benefits scandal",
        "A tax-benefit enforcement system flags families for fraud and aggressively claws back childcare support, disproportionately harming minority and dual-national households.",
        "Years of complaints, journalism, parliamentary pressure, and court findings expose systemic discrimination and administrative cruelty.",
        ["tax authority", "cabinet", "affected parents", "courts", "rights investigators"],
        ["institutional denial", "data discrimination", "family financial ruin", "election-cycle accountability"],
        ["records are complex", "compensation requires case review", "cabinet responsibility is diffuse", "public trust can collapse"],
        ["whether leaders resign", "whether compensation scales", "whether algorithmic discrimination is acknowledged"],
        "government_resignation_compensation_and_long_tail_repair",
        {"government_resignation_compensation_and_long_tail_repair": 0.62, "administrative_compensation_only": 0.18, "limited_inquiry_no_resignation": 0.12, "denial_persists": 0.08},
        [
            src("Dutch childcare benefits scandal", "https://en.wikipedia.org/wiki/Dutch_childcare_benefits_scandal"),
            src("Government of the Netherlands: cabinet resigns", "https://www.government.nl/latest/news/2021/01/15/government-resigns-over-childcare-benefit-scandal"),
            src("Amnesty: Xenophobic machines", "https://www.amnesty.org/en/latest/news/2021/10/xenophobic-machines-dutch-child-benefit-scandal/"),
        ],
    ),
    ev(
        "labor_weeks_auto_strike",
        "labor/social movements",
        "weeks",
        "2023 United Auto Workers strike",
        "A union begins targeted strikes against three major manufacturers after years of wage pressure and resentment over tiers and concessions.",
        "The union uses rolling walkouts to conserve strike funds and create uncertainty, while companies face production losses and political scrutiny.",
        ["union leadership", "rank-and-file workers", "three manufacturers", "suppliers", "national political leaders"],
        ["strike leverage", "inflation", "tier resentment", "supply-chain exposure"],
        ["contracts require ratification", "companies can absorb some stoppages but not indefinite disruption", "public sympathy favors workers initially"],
        ["whether the union expands strikes", "whether one employer settles first", "whether wage gains reset sector norms"],
        "tentative_contracts_with_major_wage_gains",
        {"tentative_contracts_with_major_wage_gains": 0.68, "modest_contracts": 0.16, "prolonged_strike_no_deal": 0.08, "employer_breakthrough": 0.08},
        [
            src("2023 United Auto Workers strike", "https://en.wikipedia.org/wiki/2023_United_Auto_Workers_strike"),
            src("UAW: tentative agreement at GM", "https://uaw.org/uaw-reaches-tentative-agreement-gm/"),
            src("Reuters: GM and UAW reach tentative agreement", "https://www.reuters.com/business/autos-transportation/gm-uaw-reach-tentative-agreement-end-strike-sources-2023-10-30/"),
        ],
    ),
    ev(
        "labor_1_3m_delivery_contract_threat",
        "labor/social movements",
        "1-3 months",
        "UPS-Teamsters 2023 contract campaign",
        "A logistics union prepares for a nationwide strike at a package delivery company as the contract deadline approaches.",
        "The company faces large economic exposure, customers can preemptively shift volume, and workers demand wage and scheduling gains.",
        ["union negotiators", "package company", "part-time workers", "large shippers", "customers"],
        ["strike deadline", "customer diversion risk", "part-time wage floor", "heat and safety grievances"],
        ["a strike would be highly disruptive", "both sides can claim wins through a tentative deal", "ratification still matters"],
        ["whether talks collapse at deadline", "whether part-time wage gains are enough", "whether customers return"],
        "strike_averted_with_tentative_contract",
        {"strike_averted_with_tentative_contract": 0.70, "short_national_strike": 0.12, "weak_deal_rejected": 0.10, "employer_concession_collapse": 0.08},
        [
            src("Teamsters: UPS tentative agreement", "https://teamster.org/2023/07/teamsters-ups-reach-historic-tentative-agreement/"),
            src("Reuters: UPS and Teamsters reach tentative deal", "https://www.reuters.com/business/ups-teamsters-reach-tentative-contract-deal-2023-07-25/"),
            src("2023 UPS strike", "https://en.wikipedia.org/wiki/2023_United_Parcel_Service_strike"),
        ],
    ),
    ev(
        "labor_3_12m_hollywood_strikes",
        "labor/social movements",
        "3-12 months",
        "2023 Hollywood labor disputes",
        "Creative workers strike against studios over streaming residuals, staffing norms, and generative technology protections.",
        "The strikes halt production, divide short-term economic pain from long-term labor standards, and force public debate about automation.",
        ["writers", "performers", "studios", "streaming platforms", "below-the-line workers"],
        ["production shutdown", "AI anxiety", "residual transparency", "solidarity across guilds"],
        ["studios can delay releases but need pipelines", "workers face income loss", "contracts can encode AI guardrails"],
        ["whether guilds stay aligned", "whether studios wait out the strike", "whether AI terms become central"],
        "new_contracts_with_pay_and_ai_guardrails",
        {"new_contracts_with_pay_and_ai_guardrails": 0.62, "partial_contract_without_ai_terms": 0.18, "strike_fragmentation": 0.10, "studio_victory": 0.10},
        [
            src("2023 Hollywood labor disputes", "https://en.wikipedia.org/wiki/2023_Hollywood_labor_disputes"),
            src("WGA: tentative agreement", "https://www.wga.org/news-events/news/press/2023/wga-reaches-tentative-agreement-with-amptp"),
            src("SAG-AFTRA contract approval", "https://www.sagaftra.org/sag-aftra-national-board-approves-tvtheatrical-contract"),
        ],
    ),
    ev(
        "labor_1_3y_coffee_union_campaign",
        "labor/social movements",
        "1-3 years",
        "Starbucks union campaign after 2021",
        "Workers at a flagship retail chain begin store-by-store union elections, creating a symbolic challenge to a company known for progressive branding.",
        "The campaign spreads quickly but bargaining is slow, legal disputes multiply, and the company can contest each store separately.",
        ["store workers", "national union", "corporate leadership", "labor board", "customers"],
        ["brand contradiction", "store-level fragmentation", "legal unfair-labor-practice claims", "first-contract difficulty"],
        ["each workplace votes separately", "the company controls scheduling and messaging", "public support does not automatically create contracts"],
        ["whether union wins convert into contracts", "whether labor board remedies matter", "whether company chooses a national path"],
        "many_election_wins_slow_contracting_then_framework",
        {"many_election_wins_slow_contracting_then_framework": 0.58, "rapid_master_contract": 0.12, "campaign_stalls": 0.18, "company_neutrality_from_start": 0.12},
        [
            src("Starbucks unions", "https://en.wikipedia.org/wiki/Starbucks_unions"),
            src("NLRB: Starbucks refused to bargain", "https://www.nlrb.gov/news-outreach/news-story/board-finds-starbucks-illegally-refused-to-recognize-and-bargain-with"),
            src("Starbucks: path forward with Workers United", "https://stories.starbucks.com/press/2024/starbucks-workers-united-and-starbucks-announce-path-forward/"),
        ],
    ),
    ev(
        "labor_3y_low_wage_campaign",
        "labor/social movements",
        "3+ years",
        "Fight for $15 movement",
        "Fast-food and low-wage workers launch a campaign around a simple wage demand that initially seems far above prevailing minimums.",
        "The movement relies on strikes, moral framing, city and state policy wins, and long-term normalization rather than one federal bargaining table.",
        ["low-wage workers", "labor organizers", "city councils", "state governments", "national parties"],
        ["simple wage frame", "local policy venues", "employer lobbying", "long-run public opinion shift"],
        ["federal law is hard to change", "cities and states can move first", "inflation changes the perceived ambition of the target"],
        ["whether the slogan diffuses", "whether local wins aggregate", "whether a federal floor changes"],
        "large_local_wage_gains_without_full_federal_target",
        {"large_local_wage_gains_without_full_federal_target": 0.64, "federal_15_minimum": 0.14, "symbolic_campaign_only": 0.12, "employer_backlash_reversal": 0.10},
        [
            src("Fight for $15", "https://en.wikipedia.org/wiki/Fight_for_$15"),
            src("Economic Policy Institute: Fight for $15 raises wages", "https://www.epi.org/publication/fight-for-15-raises-wages/"),
            src("NELP: Fight for $15 impact report", "https://www.nelp.org/insights-research/fight-for-15-impact-report/"),
        ],
    ),
    ev(
        "election_weeks_certification_pressure",
        "elections/legitimacy",
        "weeks",
        "Attempts to overturn the 2020 U.S. presidential election",
        "An incumbent loses a national election and allies pressure courts, local officials, and legislative actors to reject certified results.",
        "The losing side claims fraud without enough evidence, while decentralized certification rules and courts create many veto points against reversal.",
        ["incumbent coalition", "winning coalition", "courts", "state election officials", "legislators"],
        ["elite pressure", "misinformation", "formal certification deadlines", "institutional independence"],
        ["state results are certified separately", "courts require evidence", "the final transfer has a constitutional date"],
        ["whether courts intervene", "whether state officials defect", "whether violence changes certification"],
        "certification_and_transfer_survive",
        {"certification_and_transfer_survive": 0.68, "delayed_but_same_transfer": 0.18, "state_level_reversal": 0.08, "constitutional_crisis_no_transfer": 0.06},
        [
            src("Attempts to overturn the 2020 U.S. election", "https://en.wikipedia.org/wiki/Attempts_to_overturn_the_2020_United_States_presidential_election"),
            src("Supreme Court order in Texas v. Pennsylvania", "https://www.supremecourt.gov/orders/courtorders/121120zr_p860.pdf"),
            src("National Archives: 2020 electoral college results", "https://www.archives.gov/electoral-college/2020"),
        ],
    ),
    ev(
        "election_1_3m_transition_dispute",
        "elections/legitimacy",
        "1-3 months",
        "Brazil 2022 presidential transition",
        "A polarized incumbent loses a presidential runoff, avoids a clear concession, and supporters question electronic voting legitimacy.",
        "Election authorities, courts, military signaling, and international recognition shape whether the transfer proceeds.",
        ["incumbent", "president-elect", "electoral court", "armed forces", "street supporters"],
        ["polarization", "institutional certification", "military ambiguity", "international recognition"],
        ["the electoral court has formal authority", "the transition calendar is short", "supporter mobilization can create disorder but not necessarily legal reversal"],
        ["whether the incumbent blocks transition", "whether court certification holds", "whether protests escalate"],
        "court_certification_and_inauguration_proceed",
        {"court_certification_and_inauguration_proceed": 0.64, "delayed_transition": 0.18, "military_or_court_intervention": 0.08, "negotiated_power_sharing": 0.10},
        [
            src("2022 Brazilian general election", "https://en.wikipedia.org/wiki/2022_Brazilian_general_election"),
            src("Brazil electoral court diplomas winners", "https://www.tse.jus.br/comunicacao/noticias/2022/Dezembro/tse-diploma-lula-e-alckmin-presidente-e-vice-presidente-eleitos-em-2022"),
            src("Reuters: Bolsonaro authorizes transition", "https://www.reuters.com/world/americas/brazils-bolsonaro-authorizes-transition-lula-without-conceding-2022-11-01/"),
        ],
    ),
    ev(
        "election_3_12m_supreme_court_petition",
        "elections/legitimacy",
        "3-12 months",
        "Kenya 2022 presidential election petition",
        "A close presidential election produces an official winner, but the losing coalition files a court petition alleging serious irregularities.",
        "The judiciary has a history of asserting independence, while the election agency is internally divided and public tension is high.",
        ["declared winner", "petitioning coalition", "supreme court", "electoral commission", "civil society monitors"],
        ["legal legitimacy", "commission dissent", "peace risk", "evidence burden"],
        ["the court has a fixed petition calendar", "a rerun is possible but disruptive", "elite acceptance matters after judgment"],
        ["whether evidence meets the court threshold", "whether commission divisions undermine confidence", "whether the losing side accepts judgment"],
        "court_upholds_result",
        {"court_upholds_result": 0.58, "court_orders_rerun": 0.22, "negotiated_settlement": 0.10, "extra_legal_crisis": 0.10},
        [
            src("2022 Kenyan general election", "https://en.wikipedia.org/wiki/2022_Kenyan_general_election"),
            src("Kenya Judiciary: presidential petition judgment summary", "https://www.judiciary.go.ke/download/presidential-election-petition-judgment-summary/"),
            src("BBC: Kenya Supreme Court upholds election result", "https://www.bbc.com/news/world-africa-62785426"),
        ],
    ),
    ev(
        "election_1_3y_annulment_rerun",
        "elections/legitimacy",
        "1-3 years",
        "Malawi 2019 election annulment and 2020 rerun",
        "An incumbent is declared winner after a disputed election marked by allegations of tally irregularities and public protest.",
        "Opposition parties challenge the result in court and civil society mobilizes for a rerun under tighter scrutiny.",
        ["incumbent", "opposition alliance", "constitutional court", "electoral commission", "protesters"],
        ["ballot integrity", "street mobilization", "judicial independence", "rerun administration"],
        ["annulment is legally available but rare", "a rerun can shift opposition coordination", "security forces may affect protest costs"],
        ["whether courts annul", "whether opposition unifies", "whether rerun legitimacy improves"],
        "annulment_rerun_opposition_win",
        {"annulment_rerun_opposition_win": 0.56, "incumbent_survives_court": 0.20, "rerun_incumbent_win": 0.14, "prolonged_unrest": 0.10},
        [
            src("2019 Malawian general election", "https://en.wikipedia.org/wiki/2019_Malawian_general_election"),
            src("2020 Malawian presidential election", "https://en.wikipedia.org/wiki/2020_Malawian_presidential_election"),
            src("BBC: Malawi opposition leader wins rerun", "https://www.bbc.com/news/world-africa-53201446"),
        ],
    ),
    ev(
        "election_3y_coup_after_disputed_vote",
        "elections/legitimacy",
        "3+ years",
        "Myanmar 2020 election and 2021 coup aftermath",
        "A ruling party wins a large election victory, military-aligned actors allege fraud, and the armed forces retain constitutional and coercive power.",
        "The dispute becomes a test of whether election legitimacy can constrain an institution with autonomous armed capacity.",
        ["civilian government", "military leadership", "election commission", "protest movement", "ethnic armed groups"],
        ["coercive asymmetry", "mass civil disobedience", "international sanctions", "armed resistance"],
        ["the military can seize institutions", "civilian legitimacy can sustain resistance", "conflict can fragment over years"],
        ["whether the military accepts results", "whether protests deter repression", "whether conflict becomes durable"],
        "military_coup_and_durable_conflict",
        {"military_coup_and_durable_conflict": 0.58, "negotiated_transition": 0.12, "civilian_government_survives": 0.10, "short_repression_then_stability": 0.20},
        [
            src("2020 Myanmar general election", "https://en.wikipedia.org/wiki/2020_Myanmar_general_election"),
            src("2021 Myanmar coup d'etat", "https://en.wikipedia.org/wiki/2021_Myanmar_coup_d%27%C3%A9tat"),
            src("Human Rights Watch: Myanmar country chapter 2025", "https://www.hrw.org/world-report/2025/country-chapters/myanmar"),
        ],
    ),
    ev(
        "health_weeks_vaccine_pause",
        "public health",
        "weeks",
        "U.S. pause of Johnson & Johnson COVID-19 vaccine, 2021",
        "Regulators pause use of a single-dose vaccine after rare clotting reports while a mass vaccination campaign is underway.",
        "Public health agencies must balance safety signal investigation against vaccine confidence and access for hard-to-reach populations.",
        ["federal regulators", "advisory committee", "state health departments", "patients", "clinicians"],
        ["rare adverse event", "trust transparency", "campaign speed", "risk communication"],
        ["alternative vaccines are available", "the pause can be short", "warnings can segment risk rather than end use"],
        ["whether the pause becomes permanent", "whether confidence damage spreads", "whether warning labels satisfy safety concerns"],
        "pause_lifted_with_warning",
        {"pause_lifted_with_warning": 0.70, "vaccine_withdrawn": 0.08, "long_pause": 0.12, "no_confidence_damage": 0.10},
        [
            src("FDA/CDC lift recommended pause", "https://www.fda.gov/news-events/press-announcements/fda-and-cdc-lift-recommended-pause-johnson-johnson-janssen-covid-19-vaccine-use-following-thorough"),
            src("CDC J&J update", "https://www.cdc.gov/coronavirus/2019-ncov/vaccines/safety/JJUpdate.html"),
            src("Johnson & Johnson COVID-19 vaccine", "https://en.wikipedia.org/wiki/Johnson_%26_Johnson_COVID-19_vaccine"),
        ],
    ),
    ev(
        "health_1_3m_measles_policy",
        "public health",
        "1-3 months",
        "Disneyland measles outbreak and California vaccine policy response",
        "A measles outbreak linked to a major attraction reveals clusters of under-vaccination and reignites debate over school vaccine exemptions.",
        "Public health officials trace cases, parents polarize around mandates, and legislators consider removing personal-belief exemptions.",
        ["state health department", "parents", "legislators", "schools", "anti-mandate activists"],
        ["visible outbreak", "child risk", "mandate backlash", "herd-immunity framing"],
        ["case counts are traceable", "state law can change exemption rules", "mobilized opponents can delay but not fully veto if majorities hold"],
        ["whether outbreak fades before policy action", "whether mandate opponents dominate hearings", "whether exemption reform passes"],
        "outbreak_contained_and_exemption_law_passes",
        {"outbreak_contained_and_exemption_law_passes": 0.60, "outbreak_only_no_policy": 0.20, "weakened_compromise": 0.12, "policy_backlash_reversal": 0.08},
        [
            src("Disneyland measles outbreak", "https://en.wikipedia.org/wiki/Disneyland_measles_outbreak"),
            src("California CDPH: SB277", "https://www.cdph.ca.gov/Programs/CID/DCDC/Pages/Immunization/SB277.aspx"),
            src("CDC MMWR: measles outbreak California", "https://www.cdc.gov/mmwr/preview/mmwrhtml/mm6406a5.htm"),
        ],
    ),
    ev(
        "health_3_12m_ebola_response",
        "public health",
        "3-12 months",
        "West African Ebola epidemic response, 2014-2016",
        "A severe hemorrhagic fever outbreak spreads across weak health systems, with fear, burial practices, and mistrust undermining containment.",
        "International response scales slowly at first, then mobilizes treatment units, surveillance, safer burial practices, and community engagement.",
        ["local health ministries", "international agencies", "families", "health workers", "neighboring states"],
        ["fear of treatment centers", "health-worker deaths", "border mobility", "international emergency funding"],
        ["behavior change is essential", "case isolation and contact tracing work only after trust improves", "response capacity takes months"],
        ["whether exponential spread continues", "whether international surge arrives", "whether community resistance declines"],
        "epidemic_declines_after_large_scale_response",
        {"epidemic_declines_after_large_scale_response": 0.58, "localized_containment_only": 0.10, "regional_catastrophe": 0.18, "vaccine_led_resolution": 0.14},
        [
            src("Western African Ebola epidemic", "https://en.wikipedia.org/wiki/Western_African_Ebola_epidemic"),
            src("WHO: Ebola outbreak 2014", "https://www.who.int/emergencies/situations/ebola-outbreak-2014"),
            src("CDC: 2014-2016 Ebola outbreak", "https://www.cdc.gov/vhf/ebola/history/2014-2016-outbreak/index.html"),
        ],
    ),
    ev(
        "health_1_3y_school_reopening_conflict",
        "public health",
        "1-3 years",
        "COVID-19 school closure and reopening conflicts",
        "A respiratory pandemic forces school closures, remote learning, mitigation debates, and conflict over when and how to reopen.",
        "Public health risks, learning loss, parent labor constraints, teacher safety, and local politics pull decisions in different directions.",
        ["school districts", "teachers unions", "parents", "public health agencies", "students"],
        ["learning loss", "infection risk", "mask fatigue", "local political polarization"],
        ["authority is decentralized", "variants change risk perception", "vaccines reduce but do not eliminate conflict"],
        ["whether closures persist", "whether mitigations stabilize reopening", "whether politics overrides health guidance"],
        "gradual_reopening_with_polarized_mandate_retreat",
        {"gradual_reopening_with_polarized_mandate_retreat": 0.56, "rapid_normalization": 0.16, "long_term_remote_default": 0.10, "uniform_public_health_consensus": 0.18},
        [
            src("Impact of COVID-19 on education", "https://en.wikipedia.org/wiki/Impact_of_the_COVID-19_pandemic_on_education"),
            src("CDC school guidance archive", "https://archive.cdc.gov/www_cdc_gov/coronavirus/2019-ncov/community/schools-childcare/index.html"),
            src("Education Week school closure map", "https://www.edweek.org/leadership/map-where-are-schools-closed/2020/07"),
        ],
    ),
    ev(
        "health_3y_water_crisis",
        "public health",
        "3+ years",
        "Flint water crisis",
        "A city changes its water source under financial pressure, residents report contamination and illness, and officials initially dismiss concerns.",
        "Scientific testing, resident mobilization, media scrutiny, and intergovernmental blame determine whether the public health crisis is acknowledged and repaired.",
        ["city officials", "state officials", "residents", "scientists", "federal regulators"],
        ["lead exposure", "racial and economic injustice", "infrastructure neglect", "official denial"],
        ["pipe replacement is slow and expensive", "legal accountability takes years", "trust may not recover after technical fixes"],
        ["whether officials acknowledge harm", "whether emergency aid arrives", "whether settlements or criminal charges follow"],
        "emergency_acknowledged_settlement_and_slow_infrastructure_repair",
        {"emergency_acknowledged_settlement_and_slow_infrastructure_repair": 0.62, "quick_technical_fix": 0.10, "denial_persists": 0.10, "full_criminal_accountability": 0.18},
        [
            src("Flint water crisis", "https://en.wikipedia.org/wiki/Flint_water_crisis"),
            src("EPA: Flint drinking water response", "https://www.epa.gov/flint"),
            src("Michigan: Flint water", "https://www.michigan.gov/flintwater"),
        ],
    ),
    ev(
        "environment_weeks_canal_blockage",
        "environment/resource",
        "weeks",
        "2021 Suez Canal obstruction",
        "A giant container ship becomes lodged sideways in a narrow global trade chokepoint, halting traffic in both directions.",
        "Salvage crews race tides, dredging capacity, tug coordination, and mounting shipping delays while global media watches minute by minute.",
        ["canal authority", "ship operator", "salvage teams", "shipping firms", "insurers"],
        ["trade bottleneck", "technical salvage uncertainty", "insurance claims", "media spectacle"],
        ["the obstruction is physical not political", "traffic queues grow daily", "refloating depends on tides and equipment"],
        ["whether refloating takes days or weeks", "whether cargo must be unloaded", "whether legal detention follows"],
        "ship_refloated_after_days_with_legal_claims",
        {"ship_refloated_after_days_with_legal_claims": 0.68, "multiweek_blockage": 0.14, "cargo_unloading_required": 0.10, "major_environmental_spill": 0.08},
        [
            src("2021 Suez Canal obstruction", "https://en.wikipedia.org/wiki/2021_Suez_Canal_obstruction"),
            src("BBC: Suez Canal ship freed", "https://www.bbc.com/news/world-middle-east-56567985"),
            src("Suez Canal Authority news", "https://www.suezcanal.gov.eg/English/MediaCenter/News/Pages/navigation-resumes.aspx"),
        ],
    ),
    ev(
        "environment_1_3m_fuel_pipeline_shutdown",
        "environment/resource",
        "1-3 months",
        "Colonial Pipeline ransomware attack",
        "A fuel pipeline operator shuts down operations after a ransomware attack, creating regional supply anxiety and panic buying.",
        "Fuel distribution, cyber incident response, ransom decisions, and federal emergency waivers determine whether shortages persist.",
        ["pipeline operator", "fuel distributors", "federal agencies", "drivers", "cyber criminals"],
        ["ransom pressure", "fuel panic", "critical infrastructure exposure", "emergency transport waivers"],
        ["pipeline restart can be quick if systems are restored", "panic buying can amplify shortages", "law enforcement may later recover funds"],
        ["whether operations restart within days", "whether cyber contagion spreads", "whether public panic outlasts supply recovery"],
        "pipeline_restarts_quickly_after_regional_shortages",
        {"pipeline_restarts_quickly_after_regional_shortages": 0.64, "prolonged_fuel_shortage": 0.14, "ransom_refusal_delays_restart": 0.12, "systemic_infrastructure_crackdown": 0.10},
        [
            src("Colonial Pipeline ransomware attack", "https://en.wikipedia.org/wiki/Colonial_Pipeline_ransomware_attack"),
            src("CISA alert: Colonial Pipeline", "https://www.cisa.gov/news-events/alerts/2021/05/11/ongoing-cyber-threat-colonial-pipeline"),
            src("Reuters: Colonial Pipeline restarts operations", "https://www.reuters.com/business/energy/colonial-pipeline-restarts-operations-after-cyberattack-2021-05-12/"),
        ],
    ),
    ev(
        "environment_3_12m_city_day_zero",
        "environment/resource",
        "3-12 months",
        "Cape Town water crisis",
        "A major city approaches a projected date when municipal taps may be shut off after drought and reservoir depletion.",
        "Officials impose restrictions, residents cut consumption, tourism and inequality narratives collide, and rainfall uncertainty remains.",
        ["city government", "residents", "water utility", "businesses", "national government"],
        ["scarcity anxiety", "behavioral conservation", "inequality in water use", "rainfall uncertainty"],
        ["demand can fall faster than new supply arrives", "rain can reset reservoirs", "public compliance depends on credibility"],
        ["whether restrictions avert shutoff", "whether panic undermines compliance", "whether rain arrives in time"],
        "day_zero_averted_by_restrictions_and_rainfall",
        {"day_zero_averted_by_restrictions_and_rainfall": 0.62, "tap_shutdown": 0.14, "new_supply_resolves": 0.10, "political_collapse": 0.14},
        [
            src("Cape Town water crisis", "https://en.wikipedia.org/wiki/Cape_Town_water_crisis"),
            src("BBC: How Cape Town avoided Day Zero", "https://www.bbc.com/news/world-africa-43568156"),
            src("City of Cape Town dam levels", "https://www.capetown.gov.za/Family%20and%20home/residential-utility-services/residential-water-and-sanitation-services/this-weeks-dam-levels"),
        ],
    ),
    ev(
        "environment_1_3y_pipeline_protest",
        "environment/resource",
        "1-3 years",
        "Dakota Access Pipeline protests",
        "An oil pipeline route near Indigenous lands and water resources triggers sustained encampment protests, legal challenges, and policing conflict.",
        "The project has permits and capital behind it, while opposition has moral legitimacy, environmental claims, and national attention.",
        ["tribal government", "pipeline company", "federal agencies", "protest camps", "state police"],
        ["treaty rights", "water risk", "construction deadlines", "policing legitimacy"],
        ["easement decisions can change by administration", "construction progress creates sunk costs", "litigation can continue after operations begin"],
        ["whether easement is denied", "whether construction completes", "whether court review later changes operation"],
        "pipeline_completed_despite_protest_with_litigation_tail",
        {"pipeline_completed_despite_protest_with_litigation_tail": 0.58, "route_cancelled": 0.18, "major_reroute": 0.14, "negotiated_tribal_benefits": 0.10},
        [
            src("Dakota Access Pipeline protests", "https://en.wikipedia.org/wiki/Dakota_Access_Pipeline_protests"),
            src("Army Corps grants easement", "https://www.usace.army.mil/Media/News-Releases/News-Release-Article-View/Article/1086310/army-statement-on-dakota-access-pipeline-easement/"),
            src("Reuters: easement granted", "https://www.reuters.com/article/us-north-dakota-pipeline/army-corps-grants-final-easement-for-dakota-access-pipeline-idUSKBN15M2DU"),
        ],
    ),
    ev(
        "environment_3y_crossborder_pipeline",
        "environment/resource",
        "3+ years",
        "Keystone XL pipeline controversy",
        "A cross-border oil pipeline expansion becomes a long-running climate, jobs, and energy-security conflict across multiple administrations.",
        "Permits, executive discretion, legal challenges, investor patience, and changing climate politics determine whether the project ever reaches construction endpoint.",
        ["pipeline sponsor", "federal executive", "environmental groups", "labor groups", "provincial and state governments"],
        ["climate symbolism", "executive permit reversals", "sunk-cost risk", "jobs framing"],
        ["cross-border permit authority is political", "administrations can reverse each other", "long delays erode project economics"],
        ["whether permit survives elections", "whether sponsor exits", "whether courts force reconsideration"],
        "permit_revoked_project_terminated",
        {"permit_revoked_project_terminated": 0.60, "project_completed": 0.16, "indefinite_legal_limbo": 0.14, "rerouted_compromise": 0.10},
        [
            src("Keystone Pipeline", "https://en.wikipedia.org/wiki/Keystone_Pipeline#Keystone_XL"),
            src("White House executive order revoking permit", "https://www.whitehouse.gov/briefing-room/presidential-actions/2021/01/20/executive-order-protecting-public-health-and-environment-and-restoring-science-to-tackle-climate-crisis/"),
            src("TC Energy terminates Keystone XL", "https://www.tcenergy.com/announcements/2021/2021-06-09-tc-energy-confirms-termination-of-keystone-xl-pipeline-project/"),
        ],
    ),
    ev(
        "corp_weeks_airline_removal",
        "corporate PR crisis",
        "weeks",
        "United Express passenger removal, 2017",
        "A passenger is violently removed from an overbooked flight and video spreads globally before the company's first response lands poorly.",
        "The airline must choose between legalistic defense, apology, compensation, and operational policy change under intense reputational pressure.",
        ["airline executives", "passenger", "airport police", "customers", "regulators"],
        ["viral video", "customer outrage", "operational overbooking norms", "settlement pressure"],
        ["public evidence is vivid", "the company can change internal policy quickly", "legal settlement can close one path but not reputational damage"],
        ["whether apology is fast enough", "whether regulators intervene", "whether settlement and policy changes defuse anger"],
        "apology_settlement_and_policy_changes",
        {"apology_settlement_and_policy_changes": 0.70, "executive_resignation": 0.08, "minor_apology_only": 0.12, "regulatory_penalty_dominates": 0.10},
        [
            src("2017 United Express passenger removal", "https://en.wikipedia.org/wiki/2017_United_Express_passenger_removal"),
            src("United newsroom statement", "https://www.united.com/en/us/newsroom/announcements/cision-125208"),
            src("Reuters: United settles", "https://www.reuters.com/article/us-ual-passenger/united-settles-with-passenger-dragged-from-plane-idUSKBN17T2OU"),
        ],
    ),
    ev(
        "corp_1_3m_beer_brand_boycott",
        "corporate PR crisis",
        "1-3 months",
        "Bud Light boycott, 2023",
        "A mass-market beverage brand partners with a polarizing influencer, triggering a politically framed consumer boycott and distributor anxiety.",
        "The company faces pressure from both inclusion advocates and conservative customers while sales data quickly reveals whether outrage converts into behavior.",
        ["brand executives", "influencer", "distributors", "retail customers", "political media"],
        ["identity backlash", "retailer shelf pressure", "brand ambiguity", "competitor substitution"],
        ["beer buyers can switch cheaply", "corporate statements can alienate both sides", "distributors feel local pressure"],
        ["whether sales recover quickly", "whether leadership changes", "whether brand positioning shifts"],
        "sustained_sales_drop_and_brand_leadership_changes",
        {"sustained_sales_drop_and_brand_leadership_changes": 0.56, "short_lived_noise": 0.18, "full_rebrand": 0.10, "values_recommitment_and_recovery": 0.16},
        [
            src("Bud Light boycott", "https://en.wikipedia.org/wiki/Bud_Light_boycott"),
            src("CNN: AB InBev sales drop", "https://www.cnn.com/2023/08/03/business/bud-light-inbev-sales/index.html"),
            src("Reuters: U.S. revenue drops after boycott", "https://www.reuters.com/business/retail-consumer/ab-inbev-us-revenue-drops-bud-light-boycott-2023-08-03/"),
        ],
    ),
    ev(
        "corp_3_12m_luxury_ad_controversy",
        "corporate PR crisis",
        "3-12 months",
        "Balenciaga 2022 advertising controversy",
        "A luxury brand releases campaign imagery that critics interpret as sexualizing children and associating products with disturbing legal documents.",
        "The brand initially blames production partners but public outrage targets creative governance and corporate responsibility.",
        ["luxury brand", "creative agencies", "parents", "fashion customers", "celebrity ambassadors"],
        ["moral shock", "visual evidence", "apology credibility", "lawsuit optics"],
        ["campaigns can be pulled quickly", "luxury demand may recover if controversy fades", "blame shifting can worsen trust"],
        ["whether the brand accepts responsibility", "whether lawsuits continue", "whether ambassadors distance themselves"],
        "campaign_pulled_apologies_and_brand_repair",
        {"campaign_pulled_apologies_and_brand_repair": 0.62, "executive_purge": 0.10, "lasting_brand_collapse": 0.12, "controversy_fades_without_reform": 0.16},
        [
            src("Balenciaga advertising controversy", "https://en.wikipedia.org/wiki/Balenciaga#2022_advertising_controversy"),
            src("New York Times: Balenciaga controversy", "https://www.nytimes.com/2022/12/02/style/balenciaga-ad-campaign-controversy.html"),
            src("Guardian: Balenciaga apologises", "https://www.theguardian.com/fashion/2022/nov/28/balenciaga-apologises-for-ads-featuring-bondage-bears-with-children"),
        ],
    ),
    ev(
        "corp_1_3y_emissions_fraud",
        "corporate PR crisis",
        "1-3 years",
        "Volkswagen emissions scandal",
        "A manufacturer is caught using software to evade emissions tests across millions of vehicles, exposing regulators and customers to systematic deception.",
        "The crisis moves from denial to global recalls, criminal probes, settlements, and strategic pivot pressure.",
        ["automaker", "regulators", "vehicle owners", "dealers", "executives"],
        ["technical fraud evidence", "global recall cost", "criminal liability", "brand trust loss"],
        ["defeat-device evidence is hard to reframe", "multiple jurisdictions can impose penalties", "customer compensation must scale"],
        ["whether executives are charged", "whether buybacks happen", "whether the company survives financially"],
        "large_settlements_recalls_and_criminal_penalties",
        {"large_settlements_recalls_and_criminal_penalties": 0.68, "limited_recall_only": 0.08, "bankruptcy_or_breakup": 0.10, "regulator_failure_no_major_penalty": 0.14},
        [
            src("Volkswagen emissions scandal", "https://en.wikipedia.org/wiki/Volkswagen_emissions_scandal"),
            src("EPA: Volkswagen violations", "https://www.epa.gov/vw"),
            src("DOJ: Volkswagen guilty plea and penalties", "https://www.justice.gov/opa/pr/volkswagen-agrees-plead-guilty-and-pay-43-billion-criminal-and-civil-penalties-six"),
        ],
    ),
    ev(
        "corp_3y_oil_spill",
        "corporate PR crisis",
        "3+ years",
        "Deepwater Horizon oil spill",
        "A catastrophic offshore drilling explosion creates a prolonged oil spill, fatalities, environmental damage, and a global reputational crisis.",
        "Containment engineering, government oversight, compensation, criminal liability, and long-term environmental litigation define the endpoint.",
        ["energy company", "federal government", "coastal communities", "contractors", "environmental groups"],
        ["visible environmental harm", "technical containment uncertainty", "victim compensation", "criminal and civil liability"],
        ["the spill must be physically capped", "cleanup takes years", "settlement scale depends on statutory liability and negotiations"],
        ["whether containment works quickly", "whether blame is shared", "whether settlement reaches record scale"],
        "record_settlement_fines_and_long_cleanup",
        {"record_settlement_fines_and_long_cleanup": 0.66, "limited_liability": 0.08, "company_collapse": 0.10, "criminal_trial_dominates": 0.16},
        [
            src("Deepwater Horizon oil spill", "https://en.wikipedia.org/wiki/Deepwater_Horizon_oil_spill"),
            src("EPA: Deepwater Horizon enforcement", "https://www.epa.gov/enforcement/deepwater-horizon-bp-gulf-mexico-oil-spill"),
            src("DOJ: BP record settlement", "https://www.justice.gov/opa/pr/bp-pay-record-20-billion-settle-claims-deepwater-horizon-oil-spill"),
        ],
    ),
    ev(
        "policy_weeks_online_blackout",
        "policy/regulatory backlash",
        "weeks",
        "SOPA/PIPA internet blackout, 2012",
        "Proposed copyright enforcement bills gain legislative momentum until technology platforms and civil society coordinate a large online protest.",
        "Lawmakers face a sudden shift from low-salience industry lobbying to highly visible constituent and platform pressure.",
        ["legislators", "rights holders", "internet platforms", "users", "civil liberties groups"],
        ["blackout coordination", "constituent calls", "free internet frame", "copyright enforcement lobbying"],
        ["bills can be delayed without formal defeat", "platforms have direct communication channels to users", "lawmakers are sensitive to visible backlash"],
        ["whether sponsors hold votes", "whether protest unity persists", "whether bills are shelved"],
        "bills_shelved_after_online_blackout",
        {"bills_shelved_after_online_blackout": 0.74, "minor_amendments_then_passage": 0.10, "temporary_delay_only": 0.12, "protest_fragmentation": 0.04},
        [
            src("Protests against SOPA and PIPA", "https://en.wikipedia.org/wiki/Protests_against_SOPA_and_PIPA"),
            src("Congress.gov: SOPA", "https://www.congress.gov/bill/112th-congress/house-bill/3261"),
            src("CNN: SOPA and PIPA postponed", "https://www.cnn.com/2012/01/20/tech/web/sopa-pipa-postponed/index.html"),
        ],
    ),
    ev(
        "policy_1_3m_fuel_tax_revolt",
        "policy/regulatory backlash",
        "1-3 months",
        "French fuel-tax Yellow Vests protests, 2018",
        "A government frames a fuel-tax increase as climate policy, but commuters and rural residents organize visible protest around cost-of-living pressure.",
        "The movement is decentralized, disruptive, and hard to satisfy with a narrow tax adjustment because broader inequality grievances surface.",
        ["national government", "rural commuters", "urban protesters", "police", "small businesses"],
        ["fuel prices", "elite legitimacy", "road blockades", "broad grievance expansion"],
        ["the tax can be suspended quickly", "movement leaders are diffuse", "concessions may invite further demands"],
        ["whether tax suspension ends protests", "whether violence polarizes opinion", "whether broader concessions follow"],
        "fuel_tax_scrapped_but_protests_continue",
        {"fuel_tax_scrapped_but_protests_continue": 0.62, "tax_passes_after_delay": 0.10, "government_resignation": 0.08, "broad_social_compromise_ends_movement": 0.20},
        [
            src("Yellow vests protests", "https://en.wikipedia.org/wiki/Yellow_vests_protests"),
            src("BBC: France scraps fuel tax rise", "https://www.bbc.com/news/world-europe-46460445"),
            src("Reuters: France scraps fuel tax hikes", "https://www.reuters.com/article/us-france-protests/france-scraps-fuel-tax-hikes-in-face-of-yellow-vest-protests-idUSKBN1O4142"),
        ],
    ),
    ev(
        "policy_3_12m_net_neutrality_repeal",
        "policy/regulatory backlash",
        "3-12 months",
        "U.S. net neutrality repeal, 2017-2019",
        "A communications regulator moves to repeal open-internet rules despite mass public comments and visible technology-sector opposition.",
        "The agency majority is aligned with repeal, while opponents shift pressure to courts, Congress, and state-level rules.",
        ["regulatory commission", "internet service providers", "technology companies", "consumers", "state governments"],
        ["public comment legitimacy", "agency majority control", "court review", "state policy substitution"],
        ["the regulator can vote despite backlash", "courts defer on many agency choices", "states can create partial replacement rules"],
        ["whether repeal is blocked in court", "whether Congress acts", "whether state rules fill the gap"],
        "federal_repeal_takes_effect_with_state_litigation_tail",
        {"federal_repeal_takes_effect_with_state_litigation_tail": 0.60, "court_blocks_repeal": 0.16, "congress_restores_rules": 0.10, "agency_compromise": 0.14},
        [
            src("Restoring Internet Freedom Order", "https://en.wikipedia.org/wiki/Restoring_Internet_Freedom_Order"),
            src("FCC: Restoring Internet Freedom", "https://www.fcc.gov/restoring-internet-freedom"),
            src("Reuters: court upholds FCC repeal", "https://www.reuters.com/article/us-usa-internet/federal-court-upholds-fcc-repeal-of-net-neutrality-rules-idUSKBN1WG4RT"),
        ],
    ),
    ev(
        "policy_1_3y_extradition_bill_protests",
        "policy/regulatory backlash",
        "1-3 years",
        "Hong Kong extradition bill protests, 2019-2020",
        "A regional government proposes extradition legislation that many residents see as exposing dissidents to an external legal system.",
        "Mass protests force withdrawal of the bill, but broader demands over policing, autonomy, and democracy escalate.",
        ["regional government", "central government", "protesters", "police", "business community"],
        ["legal autonomy fears", "leader legitimacy", "policing violence", "central authority"],
        ["bill withdrawal is possible", "movement demands can expand", "central government has coercive and legislative capacity"],
        ["whether withdrawal satisfies protesters", "whether repression escalates", "whether autonomy rules change"],
        "bill_withdrawn_followed_by_security_crackdown",
        {"bill_withdrawn_followed_by_security_crackdown": 0.58, "bill_withdrawal_resolves_crisis": 0.14, "negotiated_democratic_reforms": 0.10, "bill_passes_without_change": 0.18},
        [
            src("2019-2020 Hong Kong protests", "https://en.wikipedia.org/wiki/2019%E2%80%932020_Hong_Kong_protests"),
            src("Hong Kong government withdrawal statement", "https://www.info.gov.hk/gia/general/201909/04/P2019090400667.htm"),
            src("BBC: Hong Kong extradition bill withdrawn", "https://www.bbc.com/news/world-asia-china-49633862"),
        ],
    ),
    ev(
        "policy_3y_eu_exit_referendum",
        "policy/regulatory backlash",
        "3+ years",
        "Brexit referendum and withdrawal",
        "A country votes narrowly to leave a supranational union, triggering party leadership changes, negotiation deadlines, and deep public division.",
        "The process requires withdrawal legislation, treaty negotiation, parliamentary votes, and choices about economic alignment.",
        ["national government", "parliament", "supranational negotiators", "businesses", "voters"],
        ["referendum mandate", "party splits", "trade friction", "identity polarization"],
        ["the vote creates political pressure but not an instant exit", "negotiations expose tradeoffs", "deadlines can be extended"],
        ["whether exit is reversed", "whether a negotiated deal passes", "whether no-deal rupture occurs"],
        "formal_exit_after_years_of_negotiation",
        {"formal_exit_after_years_of_negotiation": 0.60, "referendum_reversed": 0.16, "no_deal_exit": 0.14, "soft_exit_customs_alignment": 0.10},
        [
            src("Brexit", "https://en.wikipedia.org/wiki/Brexit"),
            src("UK government: Brexit what you need to know", "https://www.gov.uk/government/news/brexit-what-you-need-to-know"),
            src("Council of the EU: withdrawal agreement", "https://www.consilium.europa.eu/en/policies/eu-relations-with-the-united-kingdom/the-eu-uk-withdrawal-agreement/"),
        ],
    ),
    ev(
        "campus_weeks_university_racism_protests",
        "campus/civil society",
        "weeks",
        "University of Missouri protests, 2015",
        "Students at a large public university protest racial incidents and leadership inaction; a hunger strike and athlete boycott intensify pressure.",
        "The administration faces donor scrutiny, football revenue risk, faculty solidarity, and national media attention.",
        ["student protesters", "university president", "athletes", "faculty", "state politicians"],
        ["racial justice legitimacy", "athletic revenue leverage", "hunger strike risk", "media amplification"],
        ["leaders can resign faster than structural reforms can occur", "student leverage is highest while attention is intense", "campus governance is diffuse"],
        ["whether athlete boycott shifts power", "whether leaders resign", "whether reforms outlast attention"],
        "top_leadership_resigns_quickly",
        {"top_leadership_resigns_quickly": 0.68, "committee_reforms_without_resignation": 0.16, "protests_fade": 0.08, "state_intervention": 0.08},
        [
            src("2015 University of Missouri protests", "https://en.wikipedia.org/wiki/2015_University_of_Missouri_protests"),
            src("New York Times: Missouri president resigns", "https://www.nytimes.com/2015/11/10/us/university-of-missouri-system-president-resigns.html"),
            src("NPR: University of Missouri president resigns", "https://www.npr.org/sections/thetwo-way/2015/11/09/455397434/university-of-missouri-system-president-resigns-amid-criticism"),
        ],
    ),
    ev(
        "campus_1_3m_president_testimony_crisis",
        "campus/civil society",
        "1-3 months",
        "Harvard president Claudine Gay resignation crisis, 2023-2024",
        "A university president faces backlash after congressional testimony on campus antisemitism, followed by intensified scrutiny of academic work.",
        "Donors, faculty, students, trustees, media, and politicians contest whether the president can remain legitimate.",
        ["university president", "governing board", "donors", "faculty", "political critics"],
        ["campus speech crisis", "donor leverage", "plagiarism allegations", "board confidence"],
        ["the board can initially defend leadership", "media cycles can compound issues", "resignation may not resolve underlying campus conflict"],
        ["whether board support holds", "whether new allegations accumulate", "whether resignation becomes the least costly endpoint"],
        "president_resigns_under_compounded_pressure",
        {"president_resigns_under_compounded_pressure": 0.58, "board_support_survives": 0.20, "formal_investigation_delay": 0.12, "donor_compromise": 0.10},
        [
            src("Claudine Gay", "https://en.wikipedia.org/wiki/Claudine_Gay"),
            src("Harvard: President Gay resigns", "https://www.harvard.edu/president/news/2024/president-gay-resigns/"),
            src("New York Times: Harvard president resigns", "https://www.nytimes.com/2024/01/02/us/harvard-president-claudine-gay-resigns.html"),
        ],
    ),
    ev(
        "campus_3_12m_encampment_crisis",
        "campus/civil society",
        "3-12 months",
        "Columbia University 2024 protest encampment crisis",
        "Students occupy a central campus space demanding divestment and protection for protest speech during an international conflict.",
        "University leaders weigh police intervention, congressional scrutiny, donor pressure, faculty backlash, graduation disruption, and student discipline.",
        ["student protesters", "university president", "trustees", "police", "faculty", "donors"],
        ["encampment visibility", "safety claims", "donor and congressional pressure", "faculty governance"],
        ["campus space can be cleared physically", "disciplinary processes continue after media leaves", "leadership legitimacy can erode across constituencies"],
        ["whether negotiated dispersal occurs", "whether police intervention escalates", "whether leadership survives the semester"],
        "encampment_cleared_and_president_later_resigns",
        {"encampment_cleared_and_president_later_resigns": 0.52, "negotiated_divestment_compromise": 0.16, "protests_fade_no_leadership_change": 0.18, "campus_shutdown_extends": 0.14},
        [
            src("2024 Columbia campus occupation", "https://en.wikipedia.org/wiki/2024_Columbia_University_pro-Palestinian_campus_occupation"),
            src("Columbia president statement", "https://president.columbia.edu/news/statement-president-shafik"),
            src("New York Times: Columbia president resigns", "https://www.nytimes.com/2024/08/14/nyregion/columbia-president-minouche-shafik-resigns.html"),
        ],
    ),
    ev(
        "campus_1_3y_statue_campaign",
        "campus/civil society",
        "1-3 years",
        "Rhodes Must Fall Oxford statue campaign",
        "Students and alumni campaign to remove a colonial-era donor statue from a historic college facade.",
        "The college faces reputational pressure, donor threats, planning constraints, heritage politics, and internal disagreement.",
        ["student campaigners", "college governing body", "donors", "heritage authorities", "alumni"],
        ["symbolic decolonization", "donor backlash", "planning permission", "institutional delay"],
        ["removal requires governance and planning steps", "delay can outlast student cohorts", "a contextualization compromise is available"],
        ["whether the statue is removed", "whether donors block action", "whether the institution chooses contextualization"],
        "statue_retained_with_contextualization_process",
        {"statue_retained_with_contextualization_process": 0.50, "statue_removed": 0.26, "campaign_fades_no_change": 0.14, "donor_backlash_forces_retention_statement": 0.10},
        [
            src("Rhodes Must Fall Oxford", "https://en.wikipedia.org/wiki/Rhodes_Must_Fall#Oxford"),
            src("Oriel College: Rhodes statue history", "https://www.oriel.ox.ac.uk/about/history/rhodes-statue/"),
            src("BBC: Oriel College will not remove Rhodes statue", "https://www.bbc.com/news/uk-england-oxfordshire-57536268"),
        ],
    ),
    ev(
        "campus_3y_movement_against_harassment",
        "campus/civil society",
        "3+ years",
        "MeToo movement",
        "Survivors and journalists expose sexual harassment and assault allegations against powerful figures, creating a viral civil-society accountability wave.",
        "Institutions must decide whether to investigate, remove leaders, revise policies, or wait for attention to fade.",
        ["survivors", "journalists", "employers", "courts", "advocacy groups"],
        ["networked testimony", "workplace liability", "reputational contagion", "backlash over due process"],
        ["legal standards vary", "public accusations can move faster than courts", "policy change is uneven across sectors"],
        ["whether disclosures sustain momentum", "whether backlash narrows reforms", "whether formal law and workplace practice shift"],
        "durable_cultural_and_workplace_policy_shift_with_backlash",
        {"durable_cultural_and_workplace_policy_shift_with_backlash": 0.62, "short_lived_media_cycle": 0.10, "legal_reform_wave_only": 0.16, "backlash_reverses_norm_change": 0.12},
        [
            src("MeToo movement", "https://en.wikipedia.org/wiki/MeToo_movement"),
            src("EEOC: sexual harassment", "https://www.eeoc.gov/sexual-harassment"),
            src("Pew Research: sexual harassment at work in the era of MeToo", "https://www.pewresearch.org/social-trends/2018/04/04/sexual-harassment-at-work-in-the-era-of-metoo/"),
        ],
    ),
    ev(
        "finance_weeks_short_squeeze",
        "finance/market confidence",
        "weeks",
        "GameStop short squeeze, 2021",
        "Retail traders coordinate around a heavily shorted stock, producing extreme price volatility and pressure on brokers and clearing systems.",
        "Broker restrictions, social-media coordination, hedge-fund losses, and regulatory scrutiny interact over a very compressed time frame.",
        ["retail traders", "short sellers", "brokerage platforms", "clearinghouse", "regulators"],
        ["short interest", "viral coordination", "collateral requirements", "market fairness narratives"],
        ["brokers must meet clearing deposits", "retail attention can reverse quickly", "hearings can follow without changing the immediate price path"],
        ["whether restrictions trigger distrust", "whether price holds", "whether systemic market failure occurs"],
        "volatile_squeeze_recedes_after_restrictions_and_hearings",
        {"volatile_squeeze_recedes_after_restrictions_and_hearings": 0.62, "sustained_price_revaluation": 0.14, "broker_failure": 0.08, "regulatory_trading_halt_crisis": 0.16},
        [
            src("GameStop short squeeze", "https://en.wikipedia.org/wiki/GameStop_short_squeeze"),
            src("SEC staff report on early 2021 equity and options market conditions", "https://www.sec.gov/files/staff-report-equity-options-market-struction-conditions-early-2021.pdf"),
            src("Reuters: GameStop shares slide", "https://www.reuters.com/business/retail-consumer/gamestop-shares-slide-after-reddit-fueled-rally-2021-02-02/"),
        ],
    ),
    ev(
        "finance_1_3m_bank_run",
        "finance/market confidence",
        "1-3 months",
        "Silicon Valley Bank collapse, 2023",
        "A regional bank with concentrated venture deposits faces losses on securities and a rapid digital bank run after a failed capital raise.",
        "Regulators must decide whether to protect uninsured deposits and find a buyer while confidence spreads to similar banks.",
        ["bank management", "depositors", "federal regulators", "potential acquirers", "other regional banks"],
        ["uninsured deposits", "social-network panic", "interest-rate losses", "systemic confidence"],
        ["depositors can flee instantly", "regulators can create a bridge bank", "a buyer can stabilize operations after seizure"],
        ["whether uninsured deposits are protected", "whether contagion spreads", "whether a buyer emerges quickly"],
        "receivership_deposit_backstop_and_sale_to_buyer",
        {"receivership_deposit_backstop_and_sale_to_buyer": 0.66, "uninsured_losses_imposed": 0.08, "systemic_regional_bank_cascade": 0.16, "private_rescue_before_failure": 0.10},
        [
            src("FDIC: Silicon Valley Bank failure", "https://www.fdic.gov/resources/resolutions/bank-failures/failed-bank-list/silicon-valley.html"),
            src("Federal Reserve SVB review", "https://www.federalreserve.gov/publications/review-of-the-federal-reserves-supervision-and-regulation-of-silicon-valley-bank.htm"),
            src("Reuters: First Citizens to acquire SVB", "https://www.reuters.com/business/finance/first-citizens-acquire-failed-silicon-valley-bank-2023-03-27/"),
        ],
    ),
    ev(
        "finance_3_12m_algorithmic_stablecoin_collapse",
        "finance/market confidence",
        "3-12 months",
        "Terra/Luna collapse, 2022",
        "An algorithmic stablecoin ecosystem promises dollar stability through a linked volatile token and very high yields.",
        "A de-peg triggers reflexive redemptions, collapsing confidence in the mechanism and spreading losses across crypto markets.",
        ["stablecoin issuer", "token holders", "exchanges", "lending platforms", "regulators"],
        ["reflexive death spiral", "yield dependence", "liquidity exit", "regulatory scrutiny"],
        ["the peg depends on confidence", "reserves are limited relative to panic", "contagion can hit exposed funds and lenders"],
        ["whether the peg is restored", "whether ecosystem token collapses", "whether enforcement follows"],
        "stablecoin_and_token_collapse_with_enforcement_tail",
        {"stablecoin_and_token_collapse_with_enforcement_tail": 0.70, "peg_restored_after_bailout": 0.08, "orderly_winddown": 0.10, "broader_crypto_systemic_rescue": 0.12},
        [
            src("Terra blockchain 2022 crash", "https://en.wikipedia.org/wiki/Terra_(blockchain)#2022_crash"),
            src("SEC charges Terraform and Do Kwon", "https://www.sec.gov/newsroom/press-releases/2023-32"),
            src("BBC: Luna cryptocurrency collapses", "https://www.bbc.com/news/technology-61425209"),
        ],
    ),
    ev(
        "finance_1_3y_crypto_exchange_fraud",
        "finance/market confidence",
        "1-3 years",
        "FTX collapse and fraud prosecution",
        "A major crypto exchange faces a liquidity crisis after doubts emerge about affiliated trading-firm exposure and customer asset safety.",
        "The exchange's founder seeks rescue financing while customers withdraw, competitors publish claims, and regulators investigate.",
        ["exchange founder", "customers", "affiliated trading firm", "competitors", "prosecutors"],
        ["liquidity mismatch", "customer asset trust", "affiliate conflicts", "criminal fraud exposure"],
        ["withdrawals can outrun rescue talks", "bankruptcy can reveal asset holes", "criminal prosecution may follow if misuse is proven"],
        ["whether rescue financing appears", "whether bankruptcy exposes fraud", "whether founder is convicted"],
        "bankruptcy_founder_conviction_and_long_asset_recovery",
        {"bankruptcy_founder_conviction_and_long_asset_recovery": 0.66, "private_rescue": 0.08, "civil_settlement_only": 0.14, "orderly_merger": 0.12},
        [
            src("Bankruptcy of FTX", "https://en.wikipedia.org/wiki/Bankruptcy_of_FTX"),
            src("DOJ: Samuel Bankman-Fried sentenced", "https://www.justice.gov/usao-sdny/pr/samuel-bankman-fried-sentenced-25-years-his-orchestration-multiple-fraudulent"),
            src("SEC charges Samuel Bankman-Fried", "https://www.sec.gov/newsroom/press-releases/2022-219"),
        ],
    ),
    ev(
        "finance_3y_sovereign_debt_crisis",
        "finance/market confidence",
        "3+ years",
        "Greek government-debt crisis",
        "A euro-area member state faces unsustainable debt, austerity demands, bank stress, public protests, and recurring bailout negotiations.",
        "Domestic democratic mandates clash with creditor conditions and fears that exit from a currency union would create wider contagion.",
        ["national government", "euro-area creditors", "IMF", "banks", "voters"],
        ["austerity fatigue", "bank run risk", "currency-union contagion", "referendum mandate"],
        ["creditors can withhold funding", "exit is technically and politically risky", "bailouts can extend crisis without resolving all debt burdens"],
        ["whether the country exits the currency union", "whether bailout terms are accepted", "whether debt relief follows"],
        "bailouts_and_austerity_keep_country_in_currency_union",
        {"bailouts_and_austerity_keep_country_in_currency_union": 0.58, "currency_union_exit": 0.18, "unconditional_debt_relief": 0.10, "sovereign_default_without_program": 0.14},
        [
            src("Greek government-debt crisis", "https://en.wikipedia.org/wiki/Greek_government-debt_crisis"),
            src("IMF: Greece Q&A", "https://www.imf.org/en/Countries/GRC/greece-qandas"),
            src("Council of the EU: Greece financial assistance programme", "https://www.consilium.europa.eu/en/policies/financial-assistance-eurozone-members/greece-programme/"),
        ],
    ),
]


def now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def append_log(title: str, bullets: list[str]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"\n## {now()} - {title}\n\n")
        for bullet in bullets:
            handle.write(f"- {bullet}\n")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render_event_markdown(event: dict[str, Any]) -> str:
    body = f"""
    # Benchmark Event {event['id']}

    Category: {event['category']}

    Horizon: {event['horizon']}

    Anonymization rule: this dossier intentionally removes real names, organizations, locations, dates that uniquely identify the original event, and direct outcome facts. Source and outcome material is kept only in `sources/{event['id']}.json`.

    ## Start-State Scenario

    {event['start']}

    ## Situation at T0

    {event['situation']}

    ## Stakeholders

    {chr(10).join(f"- {item}" for item in event['stakeholders'])}

    ## Incentives and Pressures

    {chr(10).join(f"- {item}" for item in event['pressures'])}

    ## Constraints

    {chr(10).join(f"- {item}" for item in event['constraints'])}

    ## Known Uncertainty at T0

    {chr(10).join(f"- {item}" for item in event['uncertainties'])}

    ## Simulation Instruction

    Simulate plausible social, institutional, market, and legitimacy dynamics from this start state. Preserve uncertainty rather than forcing a single endpoint early. Do not assume the real-world case or import named facts from outside this dossier. When branches are warranted, branch on materially different endpoint mechanisms, not only on tone or messaging.
    """
    return textwrap.dedent(body).strip() + "\n"


def make_actor_payload(event: dict[str, Any], mode: str) -> dict[str, Any]:
    complexity = {"lean": 0, "baseline": 1, "high": 2}.get(mode, 1)
    actor_count = {"lean": 4, "baseline": 6, "high": 8}.get(mode, 6)
    cohort_count = {"lean": 5, "baseline": 8, "high": 11}.get(mode, 8)
    hero_count = {"lean": 1, "baseline": 3, "high": 5}.get(mode, 3)

    actors = [
        {"name": f"Institution {i+1}", "actor_type": "institution", "goals": [goal, "preserve legitimacy"]}
        for i, goal in enumerate((event["pressures"] + event["constraints"])[:actor_count])
    ]
    cohorts = []
    for i, item in enumerate((event["stakeholders"] + event["pressures"] + event["uncertainties"])[:cohort_count]):
        cohorts.append(
            {
                "name": f"Cohort {i+1}",
                "state": {
                    "salience": round(0.45 + (i % 5) * 0.09, 2),
                    "trust": round(0.62 - (i % 4) * 0.11, 2),
                    "mobilization": round(0.35 + (i % 6) * 0.08, 2),
                    "description": item,
                },
            }
        )
    heroes = [
        {
            "name": f"Bridge Actor {i+1}",
            "definition": {"role": item[:80]},
            "state": {"network_reach": round(0.55 + i * 0.07, 2), "risk_tolerance": round(0.35 + i * 0.08, 2)},
        }
        for i, item in enumerate((event["uncertainties"] + event["stakeholders"])[:hero_count])
    ]
    if complexity >= 2:
        for cohort in cohorts:
            cohort["state"]["memory"] = "Carries detailed local knowledge and reacts strongly to perceived procedural legitimacy."
    return {"actors": actors, "cohorts": cohorts, "heroes": heroes}


def generate_benchmark(_: argparse.Namespace) -> None:
    for directory in (EVENTS_DIR, SOURCES_DIR, RUNS_DIR, ANALYSIS_DIR, LATEX_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    index = []
    for event in EVENTS:
        (EVENTS_DIR / f"{event['id']}.md").write_text(render_event_markdown(event), encoding="utf-8")
        source_payload = {
            "id": event["id"],
            "real_case": event["real_case"],
            "category": event["category"],
            "horizon": event["horizon"],
            "actual_outcome": event["actual_outcome"],
            "expected_distribution": event["expected_distribution"],
            "sources": event["sources"],
        }
        write_json(SOURCES_DIR / f"{event['id']}.json", source_payload)
        actor_payload = make_actor_payload(event, "baseline")
        write_json(EVENTS_DIR / f"{event['id']}.actors.json", actor_payload["actors"])
        write_json(EVENTS_DIR / f"{event['id']}.cohorts.json", actor_payload["cohorts"])
        write_json(EVENTS_DIR / f"{event['id']}.heroes.json", actor_payload["heroes"])
        index.append(
            {
                "id": event["id"],
                "category": event["category"],
                "horizon": event["horizon"],
                "event_file": f"events/{event['id']}.md",
                "source_file": f"sources/{event['id']}.json",
            }
        )
    write_json(STUDY / "benchmark-index.json", index)
    write_json(ANALYSIS_DIR / "parameter_matrix.json", parameter_matrix())
    append_log(
        "Benchmark Generated",
        [
            f"Generated {len(EVENTS)} anonymized event dossiers in `events/`.",
            "Generated matching hidden source/outcome files in `sources/`.",
            "Generated baseline actor, cohort, and hero payloads for reproducible manual initialization.",
            "Generated `benchmark-index.json` and `analysis/parameter_matrix.json`.",
        ],
    )


def validate_benchmark(_: argparse.Namespace) -> None:
    errors = []
    if len(EVENTS) != 50:
        errors.append(f"expected 50 events, found {len(EVENTS)}")
    matrix = Counter((event["category"], event["horizon"]) for event in EVENTS)
    for category in CATEGORIES:
        for horizon in HORIZONS:
            if matrix[(category, horizon)] != 1:
                errors.append(f"matrix cell {category} / {horizon} has {matrix[(category, horizon)]} events")
    for event in EVENTS:
        if len(event["sources"]) < 3:
            errors.append(f"{event['id']} has fewer than 3 sources")
        total = sum(float(v) for v in event["expected_distribution"].values())
        if abs(total - 1.0) > 0.001:
            errors.append(f"{event['id']} distribution sums to {total}")
        if event["actual_outcome"] not in event["expected_distribution"]:
            errors.append(f"{event['id']} actual outcome missing from distribution")
    result = {"ok": not errors, "errors": errors, "matrix": {f"{k[0]}|{k[1]}": v for k, v in matrix.items()}}
    write_json(ANALYSIS_DIR / "benchmark_validation.json", result)
    append_log(
        "Benchmark Validation",
        [
            f"Validation result: {'ok' if not errors else 'failed'}",
            f"Matrix cells checked: {len(CATEGORIES) * len(HORIZONS)}.",
            f"Error count: {len(errors)}.",
            "Details written to `analysis/benchmark_validation.json`.",
        ],
    )
    if errors:
        raise SystemExit("\n".join(errors))


def parameter_matrix() -> dict[str, Any]:
    return {
        "baseline": {
            "tick_count": "baseline",
            "agent_count": "baseline",
            "complexity": "baseline",
            "branching_threshold": "default",
            "max_ticks_by_horizon": {"weeks": 3, "1-3 months": 4, "3-12 months": 5, "1-3 years": 6, "3+ years": 7},
            "max_active_multiverses": 8,
            "max_branch_depth": 2,
            "max_branches_per_tick": 2,
            "branch_score_threshold": 0.7,
        },
        "sweep": [
            {"name": "short_compressed_permissive", "tick_count": "short", "agent_count": "compressed", "complexity": "lean", "branching_threshold": "permissive"},
            {"name": "long_high_detail_strict", "tick_count": "long", "agent_count": "high", "complexity": "high", "branching_threshold": "strict"},
            {"name": "short_high_detail_default", "tick_count": "short", "agent_count": "high", "complexity": "high", "branching_threshold": "default"},
            {"name": "long_compressed_default", "tick_count": "long", "agent_count": "compressed", "complexity": "lean", "branching_threshold": "default"},
        ],
        "sweep_subset_size": 16,
        "subset_rule": "first 16 events sorted by category order and horizon order, producing balanced coverage over categories and horizons as far as 16 slots allows",
    }


def config_for(event: dict[str, Any], variant: dict[str, Any] | None) -> dict[str, Any]:
    base = parameter_matrix()["baseline"]
    tick_map = dict(base["max_ticks_by_horizon"])
    max_ticks = int(tick_map[event["horizon"]])
    max_active = int(base["max_active_multiverses"])
    max_depth = int(base["max_branch_depth"])
    max_branches = int(base["max_branches_per_tick"])
    threshold = float(base["branch_score_threshold"])
    complexity = "baseline"
    if variant:
        if variant["tick_count"] == "short":
            max_ticks = max(2, max_ticks - 2)
        elif variant["tick_count"] == "long":
            max_ticks = max_ticks + 3
        if variant["agent_count"] == "compressed":
            max_active = 5
        elif variant["agent_count"] == "high":
            max_active = 12
            max_depth = 3
        if variant["branching_threshold"] == "permissive":
            threshold = 0.45
        elif variant["branching_threshold"] == "strict":
            threshold = 0.85
        complexity = {"lean": "lean", "baseline": "baseline", "high": "high"}[variant["complexity"]]
    return {
        "max_ticks": max_ticks,
        "tick_duration_minutes": 1440,
        "max_schedule_horizon_ticks": max_ticks,
        "max_active_multiverses": max_active,
        "max_branch_depth": max_depth,
        "max_branches_per_tick": max_branches,
        "branch_score_threshold": threshold,
        "complexity": complexity,
    }


def run_command(command: list[str], *, env: dict[str, str] | None = None, timeout: float | None = None) -> tuple[int, str, str, float]:
    start = time.monotonic()
    try:
        proc = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr, time.monotonic() - start
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        note = f"command timed out after {timeout} seconds"
        stderr = f"{stderr}\n{note}".strip()
        return 124, stdout, stderr, time.monotonic() - start


def wf_base_command(base_url: str, timeout_seconds: int = 240) -> list[str]:
    return ["worldfork", "--base-url", base_url, "--timeout", str(timeout_seconds), "--json"]


def save_command_record(path: Path, command: list[str], code: int, stdout: str, stderr: str, elapsed: float) -> None:
    write_json(
        path,
        {
            "command": command,
            "exit_code": code,
            "elapsed_seconds": round(elapsed, 3),
            "stdout": stdout,
            "stderr": stderr,
            "recorded_at": now(),
        },
    )


ACTIVE_MULTIVERSE_STATUSES = {"active", "candidate"}


def is_active_multiverse(multiverse: dict[str, Any]) -> bool:
    return str(multiverse.get("status") or "").lower() in ACTIVE_MULTIVERSE_STATUSES


def command_record_name(prefix: str, sequence: int, label: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_")
    return f"{prefix}_{sequence:03d}_{clean}.json"


def transient_tick_failure(code: int, stdout: str, stderr: str) -> bool:
    text = f"{stdout}\n{stderr}".lower()
    return code == 124 or "503" in text or "service unavailable" in text or "timed out" in text or "timeout" in text


def load_failed_run_records(only_event_id: str | None = None) -> list[tuple[Path, dict[str, Any]]]:
    failed: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(RUNS_DIR.glob("**/run_record.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if only_event_id and record.get("event_id") != only_event_id:
            continue
        if record.get("status") != "completed" and record.get("big_bang_id"):
            failed.append((path, record))
    return failed


def event_by_id(event_id: str) -> dict[str, Any] | None:
    return next((event for event in EVENTS if event["id"] == event_id), None)


def fetch_multiverses(run_dir: Path, prefix: str, sequence: int, args: argparse.Namespace, big_bang_id: str, label: str) -> tuple[int, list[dict[str, Any]], int]:
    command = wf_base_command(args.base_url, args.timeout) + ["query", "GET", f"/api/big-bangs/{big_bang_id}/multiverses"]
    code, stdout, stderr, elapsed = run_command(command, timeout=args.timeout)
    save_command_record(run_dir / command_record_name(prefix, sequence, label), command, code, stdout, stderr, elapsed)
    if code != 0:
        return code, [], sequence + 1
    try:
        multiverses = parse_cli_json(stdout)
    except Exception:
        return 1, [], sequence + 1
    if not isinstance(multiverses, list):
        return 1, [], sequence + 1
    return 0, multiverses, sequence + 1


def run_tick_with_retries(
    run_dir: Path,
    prefix: str,
    sequence: int,
    args: argparse.Namespace,
    run_id: str,
    multiverse: dict[str, Any],
    request_index: int,
    remaining_requests: int,
) -> tuple[int, str, str, int, int]:
    attempts = max(1, min(int(args.retry_attempts), remaining_requests))
    last_code = 1
    last_stdout = ""
    last_stderr = ""
    for attempt in range(1, attempts + 1):
        key = f"resume-{run_id}-{request_index}-{attempt}-{uuid.uuid4()}"
        command = wf_base_command(args.base_url, args.timeout) + [
            "query",
            "POST",
            f"/api/multiverses/{multiverse['id']}/simulate-next-tick",
            "--data",
            json.dumps({"idempotency_key": key}),
        ]
        label = f"tick_{request_index:03d}_attempt_{attempt}_{multiverse.get('ui_label', multiverse['id'])}"
        last_code, last_stdout, last_stderr, elapsed = run_command(command, timeout=args.timeout + 60)
        save_command_record(run_dir / command_record_name(prefix, sequence, label), command, last_code, last_stdout, last_stderr, elapsed)
        sequence += 1
        if last_code == 0:
            return last_code, last_stdout, last_stderr, sequence, attempt
        if attempt >= attempts or not transient_tick_failure(last_code, last_stdout, last_stderr):
            return last_code, last_stdout, last_stderr, sequence, attempt
        sleep_seconds = min(float(args.retry_sleep_seconds) * (2 ** (attempt - 1)), float(args.retry_sleep_seconds) * attempts)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    return last_code, last_stdout, last_stderr, sequence, attempts


def refresh_model_audit(run_dir: Path, prefix: str, sequence: int, args: argparse.Namespace, big_bang_id: str) -> tuple[dict[str, Any], int]:
    logs_command = ["worldfork", "--verbosity", "full"] + wf_base_command(args.base_url, args.timeout)[1:] + [
        "logs",
        "list",
        "--run-id",
        big_bang_id,
        "--source",
        "llm",
        "--limit",
        "500",
    ]
    code, stdout, stderr, elapsed = run_command(logs_command, timeout=args.timeout)
    save_command_record(run_dir / command_record_name(prefix, sequence, "llm_logs"), logs_command, code, stdout, stderr, elapsed)
    model_audit = audit_models(stdout if code == 0 else "")
    write_json(run_dir / "model_audit.json", model_audit)
    return model_audit, sequence + 1


def regenerate_reports_and_audit(
    run_dir: Path,
    prefix: str,
    sequence: int,
    args: argparse.Namespace,
    event: dict[str, Any],
    record: dict[str, Any],
    big_bang_id: str,
    multiverses: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str, dict[str, Any], int]:
    variant_name = str(record.get("variant") or "unknown")
    for multiverse in multiverses:
        report_command = wf_base_command(args.base_url, args.timeout) + [
            "query",
            "POST",
            f"/api/multiverses/{multiverse['id']}/report",
            "--data",
            json.dumps({"title": f"Accuracy timeline report {event['id']} {multiverse.get('ui_label')}", "summary": "Generated by local overnight accuracy harness resume."}),
        ]
        code, stdout, stderr, elapsed = run_command(report_command, timeout=args.timeout + 60)
        label = f"report_multiverse_{multiverse.get('ui_label', multiverse['id'])}"
        save_command_record(run_dir / command_record_name(prefix, sequence, label), report_command, code, stdout, stderr, elapsed)
        sequence += 1

    final_command = wf_base_command(args.base_url, args.timeout) + [
        "query",
        "POST",
        f"/api/big-bangs/{big_bang_id}/reports/final",
        "--data",
        json.dumps({"title": f"Accuracy final report {event['id']} {variant_name}", "summary": "Generated by local overnight accuracy harness resume."}),
    ]
    code, stdout, stderr, elapsed = run_command(final_command, timeout=args.timeout + 60)
    save_command_record(run_dir / command_record_name(prefix, sequence, "final_report"), final_command, code, stdout, stderr, elapsed)
    sequence += 1
    final_report = None
    if code == 0:
        try:
            final_report = parse_cli_json(stdout)
        except Exception:
            final_report = None

    markdown = ""
    if final_report and final_report.get("id"):
        view_command = wf_base_command(args.base_url, args.timeout) + ["reports", "view", final_report["id"]]
        code, stdout, stderr, elapsed = run_command(view_command, timeout=args.timeout)
        save_command_record(run_dir / command_record_name(prefix, sequence, "final_report_markdown"), view_command, code, stdout, stderr, elapsed)
        sequence += 1
        if code == 0:
            markdown = stdout
            (run_dir / "final_report.md").write_text(markdown, encoding="utf-8")

    model_audit, sequence = refresh_model_audit(run_dir, prefix, sequence, args, big_bang_id)
    return final_report, markdown, model_audit, sequence


def run_health(args: argparse.Namespace) -> None:
    run_dir = RUNS_DIR / "health"
    run_dir.mkdir(parents=True, exist_ok=True)
    commands = [
        ("agent_discover", wf_base_command(args.base_url)[:1] + ["--verbosity", "summary"] + wf_base_command(args.base_url)[1:] + ["agent", "discover"]),
        ("status", wf_base_command(args.base_url)[:1] + ["--verbosity", "summary"] + wf_base_command(args.base_url)[1:] + ["status"]),
        ("models_defaults", wf_base_command(args.base_url)[:1] + ["--verbosity", "summary"] + wf_base_command(args.base_url)[1:] + ["models", "defaults"]),
        ("readyz", wf_base_command(args.base_url) + ["query", "GET", "/readyz", "--no-api-prefix"]),
    ]
    failures = []
    for name, command in commands:
        code, stdout, stderr, elapsed = run_command(command, timeout=args.timeout)
        save_command_record(run_dir / f"{name}.json", command, code, stdout, stderr, elapsed)
        if code != 0:
            failures.append(name)
    append_log(
        "Runtime Health Commands",
        [
            f"Target base URL: `{args.base_url}`.",
            "Ran `agent discover`, `status`, `models defaults`, and root `/readyz` through the `worldfork` CLI.",
            f"Failures: {', '.join(failures) if failures else 'none'}.",
            "Raw command records written under `runs/health/`.",
        ],
    )
    if failures:
        raise SystemExit(f"health command failures: {failures}")


def parse_cli_json(stdout: str) -> Any:
    text = stdout.strip()
    if not text:
        return None
    return json.loads(text)


def ensure_actor_payloads(event: dict[str, Any], complexity: str) -> tuple[Path, Path, Path]:
    payload = make_actor_payload(event, {"lean": "lean", "baseline": "baseline", "high": "high"}[complexity])
    suffix = complexity
    actors = EVENTS_DIR / f"{event['id']}.{suffix}.actors.json"
    cohorts = EVENTS_DIR / f"{event['id']}.{suffix}.cohorts.json"
    heroes = EVENTS_DIR / f"{event['id']}.{suffix}.heroes.json"
    write_json(actors, payload["actors"])
    write_json(cohorts, payload["cohorts"])
    write_json(heroes, payload["heroes"])
    return actors, cohorts, heroes


def run_one(event: dict[str, Any], run_kind: str, variant: dict[str, Any] | None, args: argparse.Namespace) -> dict[str, Any]:
    cfg = config_for(event, variant)
    variant_name = variant["name"] if variant else "baseline"
    run_id = f"{run_kind}_{variant_name}_{event['id']}"
    run_dir = RUNS_DIR / run_kind / variant_name / event["id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    simulation_config = {"max_ticks": cfg["max_ticks"], "tick_duration_minutes": cfg["tick_duration_minutes"], "max_schedule_horizon_ticks": cfg["max_schedule_horizon_ticks"]}
    branch_policy = {
        "max_branch_depth": cfg["max_branch_depth"],
        "max_active_multiverses": cfg["max_active_multiverses"],
        "max_branches_per_tick": cfg["max_branches_per_tick"],
        "branch_score_threshold": cfg["branch_score_threshold"],
        "idle_termination_ticks": max(2, math.ceil(cfg["max_ticks"] / 2)),
    }
    model_config = {"model": GEMINI, "provider": "openrouter"}
    write_json(run_dir / "simulation_config.json", simulation_config)
    write_json(run_dir / "branch_policy.json", branch_policy)
    write_json(run_dir / "model_config.json", model_config)
    actors, cohorts, heroes = ensure_actor_payloads(event, cfg["complexity"])

    command = wf_base_command(args.base_url, args.timeout) + [
        "init",
        "--name",
        f"Accuracy {event['id']} {variant_name}",
        "--description",
        f"Local accuracy evaluation run {run_id}",
        "--scenario-file",
        str(EVENTS_DIR / f"{event['id']}.md"),
        "--simulation-config",
        f"@{run_dir / 'simulation_config.json'}",
        "--branch-policy",
        f"@{run_dir / 'branch_policy.json'}",
        "--model-config",
        f"@{run_dir / 'model_config.json'}",
        "--actors",
        f"@{actors}",
        "--cohorts",
        f"@{cohorts}",
        "--heroes",
        f"@{heroes}",
        "--no-initializer-agent",
        "--wait-timeout",
        str(args.timeout),
    ]
    code, stdout, stderr, elapsed = run_command(command, timeout=args.timeout + 60)
    save_command_record(run_dir / "01_init.json", command, code, stdout, stderr, elapsed)
    if code != 0:
        return {"run_id": run_id, "event_id": event["id"], "status": "init_failed", "error": stderr[-1200:]}
    created = parse_cli_json(stdout)
    big_bang = created["data"]["big_bang"] if "data" in created else created["big_bang"]
    big_bang_id = str(big_bang["id"])

    resume_command = wf_base_command(args.base_url, args.timeout) + ["query", "POST", f"/api/big-bangs/{big_bang_id}/resume"]
    code, stdout, stderr, elapsed = run_command(resume_command, timeout=args.timeout)
    save_command_record(run_dir / "02_resume.json", resume_command, code, stdout, stderr, elapsed)

    state_command = wf_base_command(args.base_url, args.timeout) + ["query", "GET", f"/api/big-bangs/{big_bang_id}/multiverses"]
    code, stdout, stderr, elapsed = run_command(state_command, timeout=args.timeout)
    save_command_record(run_dir / "03_multiverses_initial.json", state_command, code, stdout, stderr, elapsed)
    multiverses = parse_cli_json(stdout) if code == 0 else []

    tick_records = []
    max_requests = max(1, cfg["max_ticks"] * max(1, cfg["max_active_multiverses"]))
    requests_used = 0
    while requests_used < max_requests:
        code, stdout, stderr, elapsed = run_command(state_command, timeout=args.timeout)
        save_command_record(run_dir / f"multiverses_{requests_used:03d}.json", state_command, code, stdout, stderr, elapsed)
        if code != 0:
            break
        multiverses = parse_cli_json(stdout)
        runnable = [m for m in multiverses if m.get("status") in {"active", "candidate"}]
        if not runnable:
            break
        for multiverse in runnable:
            if requests_used >= max_requests:
                break
            tick_command = wf_base_command(args.base_url, args.timeout) + [
                "query",
                "POST",
                f"/api/multiverses/{multiverse['id']}/simulate-next-tick",
                "--data",
                json.dumps({"idempotency_key": f"{run_id}-{requests_used}-{int(time.time())}"}),
            ]
            code, stdout, stderr, elapsed = run_command(tick_command, timeout=args.timeout + 60)
            save_command_record(run_dir / f"tick_{requests_used:03d}.json", tick_command, code, stdout, stderr, elapsed)
            tick_records.append({"code": code, "stdout": stdout, "stderr": stderr})
            requests_used += 1
            if code != 0:
                break
        if tick_records and tick_records[-1]["code"] != 0:
            break

    code, stdout, stderr, elapsed = run_command(state_command, timeout=args.timeout)
    save_command_record(run_dir / "04_multiverses_final.json", state_command, code, stdout, stderr, elapsed)
    multiverses = parse_cli_json(stdout) if code == 0 else []
    for multiverse in multiverses:
        report_command = wf_base_command(args.base_url, args.timeout) + [
            "query",
            "POST",
            f"/api/multiverses/{multiverse['id']}/report",
            "--data",
            json.dumps({"title": f"Accuracy timeline report {event['id']} {multiverse.get('ui_label')}", "summary": "Generated by local overnight accuracy harness."}),
        ]
        code, stdout, stderr, elapsed = run_command(report_command, timeout=args.timeout + 60)
        save_command_record(run_dir / f"report_multiverse_{multiverse.get('ui_label', multiverse['id'])}.json", report_command, code, stdout, stderr, elapsed)

    final_command = wf_base_command(args.base_url, args.timeout) + [
        "query",
        "POST",
        f"/api/big-bangs/{big_bang_id}/reports/final",
        "--data",
        json.dumps({"title": f"Accuracy final report {event['id']} {variant_name}", "summary": "Generated by local overnight accuracy harness."}),
    ]
    code, stdout, stderr, elapsed = run_command(final_command, timeout=args.timeout + 60)
    save_command_record(run_dir / "05_final_report.json", final_command, code, stdout, stderr, elapsed)
    final_report = parse_cli_json(stdout) if code == 0 else None

    markdown = ""
    if final_report and final_report.get("id"):
        view_command = wf_base_command(args.base_url, args.timeout) + ["reports", "view", final_report["id"]]
        code, stdout, stderr, elapsed = run_command(view_command, timeout=args.timeout)
        save_command_record(run_dir / "06_final_report_markdown.json", view_command, code, stdout, stderr, elapsed)
        if code == 0:
            markdown = stdout
            (run_dir / "final_report.md").write_text(markdown, encoding="utf-8")

    logs_command = ["worldfork", "--verbosity", "full"] + wf_base_command(args.base_url, args.timeout)[1:] + [
        "logs",
        "list",
        "--run-id",
        big_bang_id,
        "--source",
        "llm",
        "--limit",
        "500",
    ]
    code, stdout, stderr, elapsed = run_command(logs_command, timeout=args.timeout)
    save_command_record(run_dir / "07_llm_logs.json", logs_command, code, stdout, stderr, elapsed)
    model_audit = audit_models(stdout if code == 0 else "")
    write_json(run_dir / "model_audit.json", model_audit)

    score = score_text(event, markdown)
    record = {
        "run_id": run_id,
        "event_id": event["id"],
        "category": event["category"],
        "horizon": event["horizon"],
        "kind": run_kind,
        "variant": variant_name,
        "status": "completed" if final_report else "report_failed",
        "big_bang_id": big_bang_id,
        "final_report_version_id": final_report.get("id") if final_report else None,
        "tick_requests": requests_used,
        "config": cfg,
        "score": score,
        "model_audit": model_audit,
        "run_dir": str(run_dir.relative_to(STUDY)),
    }
    write_json(run_dir / "run_record.json", record)
    return record


def audit_models(stdout: str) -> dict[str, Any]:
    try:
        payload = parse_cli_json(stdout)
    except Exception:
        return {"ok": False, "reason": "could_not_parse_logs", "calls": 0, "models": []}
    rows = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    if not isinstance(rows, list):
        return {"ok": False, "reason": "logs_payload_not_list", "calls": 0, "models": []}
    models = sorted({str(row.get("model")) for row in rows if row.get("model")})
    non_gemini = [model for model in models if model != GEMINI]
    return {"ok": not non_gemini, "calls": len(rows), "models": models, "non_gemini": non_gemini}


def tokenize(text: str) -> set[str]:
    stop = {"and", "or", "the", "with", "after", "into", "from", "that", "this", "only", "then", "than", "under"}
    return {tok for tok in re.findall(r"[a-z0-9]+", text.lower().replace("_", " ")) if len(tok) > 2 and tok not in stop}


def score_text(event: dict[str, Any], markdown: str) -> dict[str, Any]:
    expected = event["expected_distribution"]
    text_tokens = tokenize(markdown)
    raw = {}
    for label in expected:
        raw[label] = len(tokenize(label) & text_tokens) + 0.5 * len(tokenize(label.replace("_", " ")) & text_tokens)
    if not raw or sum(raw.values()) == 0:
        observed = {label: 1.0 / len(expected) for label in expected}
    else:
        total = sum(raw.values())
        observed = {label: raw[label] / total for label in expected}
    top_expected = max(expected, key=expected.get)
    top_observed = max(observed, key=observed.get)
    actual = event["actual_outcome"]
    tvd = 0.5 * sum(abs(float(expected[label]) - float(observed.get(label, 0))) for label in expected)
    ranked = sorted(observed.items(), key=lambda item: item[1], reverse=True)
    actual_rank = next((i + 1 for i, (label, _) in enumerate(ranked) if label == actual), len(ranked))
    return {
        "top_outcome_match": top_observed == actual,
        "top_expected_label": top_expected,
        "top_observed_label": top_observed,
        "actual_outcome": actual,
        "actual_outcome_rank": actual_rank,
        "assigned_probability_to_actual": round(float(observed.get(actual, 0.0)), 4),
        "total_variation_distance": round(tvd, 4),
        "observed_distribution": {k: round(v, 4) for k, v in observed.items()},
        "scoring_method": "token-overlap against final report markdown; ambiguous low-signal outputs should be manually reviewed",
    }


def selected_events(limit: int | None = None) -> list[dict[str, Any]]:
    order = {cat: i for i, cat in enumerate(CATEGORIES)}
    horder = {h: i for i, h in enumerate(HORIZONS)}
    rows = sorted(EVENTS, key=lambda e: (order[e["category"]], horder[e["horizon"]]))
    return rows[:limit] if limit else rows


def run_baseline(args: argparse.Namespace) -> None:
    records = []
    errors = []
    for event in selected_events(args.limit):
        existing = RUNS_DIR / "baseline" / "baseline" / event["id"] / "run_record.json"
        if existing.exists():
            record = json.loads(existing.read_text())
            records.append(record)
            if record.get("status") != "completed":
                errors.append(record)
            continue
        record = run_one(event, "baseline", None, args)
        records.append(record)
        if record.get("status") != "completed":
            errors.append(record)
        write_json(RUNS_DIR / "baseline" / "baseline_records.json", records)
    append_log(
        "Baseline Runs",
        [
            f"Attempted baseline runs: {len(records)}.",
            f"Completed: {sum(1 for item in records if item.get('status') == 'completed')}.",
            f"Failures: {len(errors)}.",
            "Aggregate records written to `runs/baseline/baseline_records.json`.",
        ],
    )


def run_sweep(args: argparse.Namespace) -> None:
    matrix = parameter_matrix()
    subset = selected_events(matrix["sweep_subset_size"])
    records = []
    for event in subset[: args.limit or len(subset)]:
        for variant in matrix["sweep"]:
            existing = RUNS_DIR / "sweep" / variant["name"] / event["id"] / "run_record.json"
            if existing.exists():
                records.append(json.loads(existing.read_text()))
                continue
            record = run_one(event, "sweep", variant, args)
            records.append(record)
            write_json(RUNS_DIR / "sweep" / "sweep_records.json", records)
    append_log(
        "Parameter Sweep Runs",
        [
            f"Attempted sweep run count: {len(records)}.",
            f"Completed: {sum(1 for item in records if item.get('status') == 'completed')}.",
            "Sweep factors: tick count, agent count, prompt complexity, branch threshold.",
            "Aggregate records written to `runs/sweep/sweep_records.json`.",
        ],
    )


def resume_failed_runs(args: argparse.Namespace) -> None:
    failed_records = load_failed_run_records(args.only)
    if not failed_records:
        print("No failed run_record.json entries found.")
        return

    resumed = []
    for record_path, record in failed_records:
        event_id = str(record.get("event_id") or "")
        event = event_by_id(event_id)
        big_bang_id = str(record.get("big_bang_id"))
        run_dir = record_path.parent
        prefix = f"resume_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"  # noqa: UP017
        sequence = 1
        requests_used = 0
        successful_ticks = 0
        consecutive_transient_failures = 0
        stop_reason = ""
        tick_failures: list[dict[str, Any]] = []
        refreshed_model_audit = False

        code, multiverses, sequence = fetch_multiverses(run_dir, prefix, sequence, args, big_bang_id, "multiverses_initial")
        multiverse_list_ok = code == 0
        initial_active = [m for m in multiverses if is_active_multiverse(m)]
        active_summary = ", ".join(f"{m.get('ui_label', m.get('id'))}:{m.get('status')}" for m in initial_active) or "none"
        print(f"{event_id} {big_bang_id} active multiverses: {active_summary}", flush=True)

        if event is None:
            record["status"] = "resume_failed"
            tick_failures.append({"reason": "event_not_found", "event_id": event_id})
        elif code != 0:
            record["status"] = "resume_failed"
            tick_failures.append({"reason": "multiverse_list_failed", "code": code})
        else:
            while requests_used < args.max_requests_per_run:
                active = [m for m in multiverses if is_active_multiverse(m)]
                if not active:
                    break
                for multiverse in active:
                    if requests_used >= args.max_requests_per_run:
                        break
                    remaining = args.max_requests_per_run - requests_used
                    code, stdout, stderr, sequence, attempts = run_tick_with_retries(
                        run_dir,
                        prefix,
                        sequence,
                        args,
                        str(record.get("run_id") or event_id),
                        multiverse,
                        requests_used,
                        remaining,
                    )
                    requests_used += attempts
                    if code == 0:
                        successful_ticks += 1
                        consecutive_transient_failures = 0
                    else:
                        transient = transient_tick_failure(code, stdout, stderr)
                        tick_failures.append(
                            {
                                "multiverse_id": multiverse.get("id"),
                                "ui_label": multiverse.get("ui_label"),
                                "status": multiverse.get("status"),
                                "code": code,
                                "transient": transient,
                                "attempts": attempts,
                                "stderr_tail": stderr[-1200:],
                            }
                        )
                        if transient:
                            consecutive_transient_failures += attempts
                            if consecutive_transient_failures >= args.max_consecutive_transient_failures:
                                stop_reason = "transient_failure_cap_reached"
                                break
                        else:
                            stop_reason = "non_transient_tick_failure"
                            break
                if stop_reason:
                    break
                list_code, multiverses, sequence = fetch_multiverses(run_dir, prefix, sequence, args, big_bang_id, f"multiverses_after_{requests_used:03d}")
                multiverse_list_ok = list_code == 0
                if list_code != 0:
                    tick_failures.append({"reason": "multiverse_list_failed", "code": list_code})
                    break

        if event is not None and multiverse_list_ok:
            final_active = [m for m in multiverses if is_active_multiverse(m)]
            if not final_active:
                final_report, markdown, model_audit, sequence = regenerate_reports_and_audit(run_dir, prefix, sequence, args, event, record, big_bang_id, multiverses)
                if final_report and final_report.get("id") and markdown:
                    record["status"] = "completed"
                    record["final_report_version_id"] = final_report.get("id")
                    record["score"] = score_text(event, markdown)
                else:
                    record["status"] = "report_failed"
                    record["final_report_version_id"] = final_report.get("id") if final_report else None
                record["model_audit"] = model_audit
                refreshed_model_audit = True
            else:
                if stop_reason:
                    record["status"] = stop_reason
                else:
                    record["status"] = "request_cap_reached" if requests_used >= args.max_requests_per_run else "tick_failed"
        else:
            final_active = [m for m in multiverses if is_active_multiverse(m)]
            record["status"] = "resume_failed"

        if not refreshed_model_audit:
            model_audit, sequence = refresh_model_audit(run_dir, prefix, sequence, args, big_bang_id)
            record["model_audit"] = model_audit

        previous_tick_requests = int(record.get("tick_requests") or 0)
        record["tick_requests"] = previous_tick_requests + successful_ticks
        resume_entry = {
            "resumed_at": now(),
            "command_prefix": prefix,
            "base_url": args.base_url,
            "timeout": args.timeout,
            "max_requests_per_run": args.max_requests_per_run,
            "retry_attempts": args.retry_attempts,
            "retry_sleep_seconds": args.retry_sleep_seconds,
            "requests_used": requests_used,
            "successful_ticks": successful_ticks,
            "consecutive_transient_failures": consecutive_transient_failures,
            "stop_reason": stop_reason,
            "previous_tick_requests": previous_tick_requests,
            "initial_active_multiverses": [
                {"id": m.get("id"), "ui_label": m.get("ui_label"), "status": m.get("status")}
                for m in initial_active
            ],
            "final_active_multiverses": [
                {"id": m.get("id"), "ui_label": m.get("ui_label"), "status": m.get("status")}
                for m in final_active
            ],
            "final_multiverse_statuses": [
                {"id": m.get("id"), "ui_label": m.get("ui_label"), "status": m.get("status")}
                for m in multiverses
            ],
            "failures": tick_failures,
        }
        history = record.get("resume_history")
        if not isinstance(history, list):
            history = []
        history.append(resume_entry)
        record["resume_history"] = history
        record["latest_resume"] = resume_entry
        write_json(record_path, record)
        resumed.append({"event_id": event_id, "status": record.get("status"), "requests_used": requests_used})

    print(json.dumps({"resumed": resumed}, indent=2, sort_keys=True), flush=True)


def load_records() -> list[dict[str, Any]]:
    records = []
    for path in RUNS_DIR.glob("**/run_record.json"):
        try:
            records.append(json.loads(path.read_text()))
        except Exception:
            continue
    return records


def aggregate_scores(_: argparse.Namespace) -> None:
    records = load_records()
    completed = [r for r in records if r.get("status") == "completed"]
    by_variant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_horizon: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in completed:
        by_variant[record["variant"]].append(record)
        by_category[record["category"]].append(record)
        by_horizon[record["horizon"]].append(record)

    def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {"count": 0}
        return {
            "count": len(rows),
            "top_match_rate": round(sum(1 for r in rows if r["score"]["top_outcome_match"]) / len(rows), 4),
            "mean_tvd": round(sum(r["score"]["total_variation_distance"] for r in rows) / len(rows), 4),
            "mean_actual_probability": round(sum(r["score"]["assigned_probability_to_actual"] for r in rows) / len(rows), 4),
            "gemini_only_runs": sum(1 for r in rows if r.get("model_audit", {}).get("ok")),
            "llm_calls": sum(int(r.get("model_audit", {}).get("calls") or 0) for r in rows),
        }

    summary = {
        "generated_at": now(),
        "records": len(records),
        "completed": len(completed),
        "overall": summarize(completed),
        "by_variant": {key: summarize(rows) for key, rows in sorted(by_variant.items())},
        "by_category": {key: summarize(rows) for key, rows in sorted(by_category.items())},
        "by_horizon": {key: summarize(rows) for key, rows in sorted(by_horizon.items())},
        "failures": [r for r in records if r.get("status") != "completed"],
    }
    write_json(ANALYSIS_DIR / "score_summary.json", summary)
    write_markdown_reports(summary, records)
    append_log(
        "Score Aggregation",
        [
            f"Loaded run records: {len(records)}.",
            f"Completed records: {len(completed)}.",
            "Wrote `analysis/score_summary.json`, `research-report.md`, and `accuracy-advice.md`.",
        ],
    )


def md_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._\n"
    out = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(row.get(col, "")) for col in columns) + " |")
    return "\n".join(out) + "\n"


def write_markdown_reports(summary: dict[str, Any], records: list[dict[str, Any]]) -> None:
    validation_path = ANALYSIS_DIR / "benchmark_validation.json"
    validation = json.loads(validation_path.read_text()) if validation_path.exists() else {}
    variant_rows = [
        {"variant": key, **value}
        for key, value in summary.get("by_variant", {}).items()
    ]
    category_rows = [
        {"category": key, **value}
        for key, value in summary.get("by_category", {}).items()
    ]
    horizon_rows = [
        {"horizon": key, **value}
        for key, value in summary.get("by_horizon", {}).items()
    ]
    failure_rows = []
    for record in summary.get("failures", []):
        latest_resume = record.get("latest_resume") if isinstance(record.get("latest_resume"), dict) else {}
        final_active = latest_resume.get("final_active_multiverses") if isinstance(latest_resume, dict) else []
        if not isinstance(final_active, list):
            final_active = []
        run_dir = STUDY / str(record.get("run_dir", ""))
        failed_tick = ""
        tick_error = ""
        final_error = ""
        if run_dir.exists():
            for tick_path in sorted(run_dir.glob("tick_*.json"), reverse=True):
                try:
                    tick_record = json.loads(tick_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if tick_record.get("exit_code") != 0:
                    failed_tick = tick_path.stem
                    tick_error = str(tick_record.get("stderr") or "").strip().replace("\n", " ")[:140]
                    break
            final_path = run_dir / "05_final_report.json"
            if final_path.exists():
                try:
                    final_record = json.loads(final_path.read_text(encoding="utf-8"))
                    final_error = str(final_record.get("stderr") or "").strip().replace("\n", " ")[:140]
                except Exception:
                    final_error = "could_not_parse_final_report_record"
            if not final_active:
                final_snapshot_path = run_dir / "04_multiverses_final.json"
                if final_snapshot_path.exists():
                    try:
                        snapshot_record = json.loads(final_snapshot_path.read_text(encoding="utf-8"))
                        multiverses = parse_cli_json(str(snapshot_record.get("stdout") or ""))
                        if isinstance(multiverses, list):
                            final_active = [item for item in multiverses if isinstance(item, dict) and is_active_multiverse(item)]
                    except Exception:
                        final_active = []
        failure_rows.append(
            {
                "event_id": record.get("event_id"),
                "variant": record.get("variant"),
                "status": record.get("status"),
                "stop_reason": latest_resume.get("stop_reason", ""),
                "failed_tick": failed_tick,
                "tick_error": tick_error,
                "final_error": final_error,
                "active_multiverses": ", ".join(str(item.get("ui_label") or item.get("id")) for item in final_active if isinstance(item, dict)),
            }
        )
    report = f"""# Overnight WorldFork Accuracy Evaluation

Generated: {now()}

Primary log: `REPRODUCIBILITY_LOG.md`

## Scope

This local-only study uses 50 anonymized concluded-event dossiers arranged as 10 categories by 5 horizons. Real source links and expected outcome distributions are stored separately under `sources/` and are not included in prompt text.

Live model constraint: `{GEMINI}`.

## Benchmark Validation

- Validation OK: `{validation.get('ok')}`
- Event count: `{len(EVENTS)}`
- Matrix cells: `{len(CATEGORIES) * len(HORIZONS)}`

## Parameter Matrix

Baseline uses medium agent count, medium description complexity, baseline tick counts by horizon, and default branch threshold. The focused sweep uses four fractional-factorial variants across tick count, agent count, prompt complexity, and branch threshold.

## Aggregate Accuracy

```json
{json.dumps(summary.get('overall', {}), indent=2)}
```

## Variant Breakdown

{md_table(variant_rows, ['variant', 'count', 'top_match_rate', 'mean_tvd', 'mean_actual_probability', 'gemini_only_runs', 'llm_calls'])}

## Category Breakdown

{md_table(category_rows, ['category', 'count', 'top_match_rate', 'mean_tvd', 'mean_actual_probability', 'gemini_only_runs', 'llm_calls'])}

## Horizon Breakdown

{md_table(horizon_rows, ['horizon', 'count', 'top_match_rate', 'mean_tvd', 'mean_actual_probability', 'gemini_only_runs', 'llm_calls'])}

## Failed/Retryable Runs

The five incomplete sweep records are operational failures, not hidden-outcome scoring failures. All occurred in `long_high_detail_strict` after late `HTTP 503 ... LLM unavailable` tick failures left one or more active multiverses. A direct final-report retry is not sufficient because `/api/big-bangs/<id>/reports/final` rejects nonterminal inputs with `HTTP 409 final report requires terminal multiverses`.

{md_table(failure_rows, ['event_id', 'variant', 'status', 'stop_reason', 'failed_tick', 'active_multiverses', 'tick_error', 'final_error'])}

The harness now provides `resume-failures`, which lists active multiverses, advances them with fresh idempotency keys, backs off on transient `503`/timeout failures, writes new `resume_*` command records, refreshes model audits, and regenerates reports when all multiverses are terminal. During verification, the live provider path still returned `503`; a targeted retry therefore exited with `transient_failure_cap_reached`, preserving a clean retryable state.

## Representative Run Records

{md_table([{k: r.get(k) for k in ['event_id', 'category', 'horizon', 'variant', 'status']} | {'top_observed': r.get('score', {}).get('top_observed_label'), 'actual_probability': r.get('score', {}).get('assigned_probability_to_actual'), 'tvd': r.get('score', {}).get('total_variation_distance')} for r in records[:25]], ['event_id', 'category', 'horizon', 'variant', 'status', 'top_observed', 'actual_probability', 'tvd'])}

## Model Audit

Every completed run writes `model_audit.json` under its run directory. The aggregate table counts a run as Gemini-only only when all audited LLM rows list exactly `{GEMINI}`.

## Scoring Caveat

Automated scoring uses token overlap between final report endpoint text and hidden outcome labels. Low-signal or surprising cases should be manually reviewed against `final_report.md`, tick records, and source files before treating the score as final.

## Accuracy Pattern Notes

- Weakest completed categories were public health and elections/legitimacy. The logs show process states such as reviews, pauses, certifications, and investigations being mistaken for endpoint movement.
- Weakest horizon was weeks. The model often under-weighted short institutional deadlines, rapid warning-lift decisions, and quick negotiated settlements.
- `short_high_detail_default` provided the best reliability-adjusted sweep signal. `long_high_detail_strict` had the best completed top-match rate but materially higher runtime failure risk and cost.
"""
    (STUDY / "research-report.md").write_text(report, encoding="utf-8")

    advice = f"""# WorldFork Accuracy Improvement Advice

Generated: {now()}

Primary log: `REPRODUCIBILITY_LOG.md`

## Current Evidence Base

- Completed records: {summary.get('completed', 0)}
- Overall top-outcome match rate: {summary.get('overall', {}).get('top_match_rate')}
- Overall mean TVD: {summary.get('overall', {}).get('mean_tvd')}
- Gemini-only completed runs: {summary.get('overall', {}).get('gemini_only_runs')}

## Recommendations

1. Treat endpoint priors as first-class scenario fields. Prompt guidance already mentions endpoint priors, so the novel step is durable schema/state: explicit authority, switching costs, coercive capacity, legal veto points, and credible remaining alternatives that survive across init, ticks, branching, and reports.

2. Add an endpoint coverage ledger. Each initializer terminal option should remain a tracked object marked `active`, `weakened`, `eliminated`, or `realized`, with tick/event/God-review evidence for every transition.

3. Separate process states from endpoint states with a report gate. Final reports should emit `unresolved`, `process_only`, `terminal_claimed`, or `terminal_verified`, and should not silently map committees, pauses, audits, negotiations, or nonterminal litigation to final outcomes.

4. Weight endpoint probabilities by authority proximity, not branch score alone. A late event from a powerless cohort should count less than a decision event from an actor with encoded veto/control authority.

5. Add a contradiction pass before endpoint selection. The report agent should ask what evidence would make the leading endpoint wrong and whether that evidence is still live in the trace.

6. Use outcome-label-aware scoring. Current reports already contain endpoint prose and outcome conclusions, but benchmark scoring still falls back to markdown token overlap. Add stable machine labels, probabilities, supporting tick IDs, blockers, and remaining uncertainty.

7. Add branch-drain and partial-report semantics. The runtime should either drain active branches before final reports or produce an explicit partial/nonterminal report that lists active multiverses. The local harness now has a retry path, but the backend should expose this as first-class operational behavior.

8. Tune default evaluation settings toward `short_high_detail_default`. It was the best cost/reliability compromise in this run; reserve `long_high_detail_strict` for smaller pilots with branch-drain safeguards.

## Potential Paths To Explore

- Compare initializer-agent runs against manual-cohort runs on a 5-event pilot to quantify whether the initializer preserves hidden endpoint priors or adds generic sociology noise.
- Add an endpoint histogram object to report content with stable labels, probability, supporting ticks, and a short causal rationale.
- Add a benchmark mode that accepts hidden expected distributions and emits evaluation-ready run metadata without exposing hidden outcomes to the simulation prompt.
- Add synthetic calibration tests: authority-dominant, switching-cost-dominant, coalition-fracture-dominant, and process-only scenarios. Assert structured endpoint state and probabilities, not prompt substrings.
"""
    (STUDY / "accuracy-advice.md").write_text(advice, encoding="utf-8")


def build_latex(args: argparse.Namespace) -> None:
    summary_path = ANALYSIS_DIR / "score_summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {"overall": {}, "by_variant": {}, "by_category": {}, "by_horizon": {}, "completed": 0}

    def latex_escape(text: Any) -> str:
        s = str(text)
        repl = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}", "\\": r"\textbackslash{}"}
        return "".join(repl.get(ch, ch) for ch in s)

    def rows(items: dict[str, Any], key_name: str) -> str:
        lines = []
        for key, value in items.items():
            lines.append(
                f"{latex_escape(key)} & {value.get('count', 0)} & {value.get('top_match_rate', '')} & {value.get('mean_tvd', '')} & {value.get('mean_actual_probability', '')} \\\\"
            )
        return "\n".join(lines) or r"\multicolumn{5}{l}{No completed rows} \\"

    failure_lines = []
    for record in summary.get("failures", []):
        run_dir = STUDY / str(record.get("run_dir", ""))
        failed_tick = ""
        active_labels: list[str] = []
        if run_dir.exists():
            for tick_path in sorted(run_dir.glob("tick_*.json"), reverse=True):
                try:
                    tick_record = json.loads(tick_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if tick_record.get("exit_code") != 0:
                    failed_tick = tick_path.stem
                    break
            latest_resume = record.get("latest_resume") if isinstance(record.get("latest_resume"), dict) else {}
            final_active = latest_resume.get("final_active_multiverses") if isinstance(latest_resume, dict) else []
            if not isinstance(final_active, list) or not final_active:
                final_snapshot_path = run_dir / "04_multiverses_final.json"
                if final_snapshot_path.exists():
                    try:
                        snapshot_record = json.loads(final_snapshot_path.read_text(encoding="utf-8"))
                        multiverses = parse_cli_json(str(snapshot_record.get("stdout") or ""))
                        if isinstance(multiverses, list):
                            final_active = [item for item in multiverses if isinstance(item, dict) and is_active_multiverse(item)]
                    except Exception:
                        final_active = []
            active_labels = [str(item.get("ui_label") or item.get("id")) for item in final_active if isinstance(item, dict)]
        failure_lines.append(
            f"{latex_escape(record.get('event_id', ''))} & {latex_escape(record.get('status', ''))} & {latex_escape(failed_tick)} & {latex_escape(', '.join(active_labels))} \\\\"
        )
    failure_table_rows = "\n".join(failure_lines) or r"\multicolumn{4}{l}{No incomplete rows} \\"

    tex = rf"""\documentclass[11pt]{{article}}
\usepackage[margin=0.8in]{{geometry}}
\usepackage{{booktabs}}
\usepackage{{longtable}}
\usepackage{{hyperref}}
\usepackage{{enumitem}}
\title{{WorldFork Overnight Accuracy Evaluation}}
\author{{Local Codex Harness}}
\date{{{latex_escape(now())}}}
\begin{{document}}
\maketitle

\section*{{Scope}}
This local-only study evaluates WorldFork against 50 anonymized concluded-event dossiers arranged as 10 categories by 5 horizons. Hidden source and outcome files stay separate from prompt dossiers. All live API-credit calls are constrained to \texttt{{{latex_escape(GEMINI)}}}.

\section*{{Primary Reproducibility Reference}}
The running log is \texttt{{agent-testing/accuracy-overnight/REPRODUCIBILITY\_LOG.md}}. Generated run records, command outputs, source files, event prompts, score tables, and this \LaTeX{{}} source are saved under \texttt{{agent-testing/accuracy-overnight/}}.

\section*{{Aggregate Results}}
\begin{{tabular}}{{lr}}
\toprule
Metric & Value \\
\midrule
Completed runs & {summary.get('completed', 0)} \\
Top-outcome match rate & {summary.get('overall', {}).get('top_match_rate', '')} \\
Mean total variation distance & {summary.get('overall', {}).get('mean_tvd', '')} \\
Mean actual-outcome probability & {summary.get('overall', {}).get('mean_actual_probability', '')} \\
Gemini-only completed runs & {summary.get('overall', {}).get('gemini_only_runs', '')} \\
Audited LLM calls & {summary.get('overall', {}).get('llm_calls', '')} \\
\bottomrule
\end{{tabular}}

\section*{{Reliability Pattern}}
Baseline completed 50/50 runs. The sweep completed 59/64 runs. All five incomplete sweep cells were \texttt{{long\_high\_detail\_strict}} and failed after late \texttt{{LLM unavailable}} tick errors left one or more active multiverses, causing the final report endpoint to reject nonterminal inputs. A direct final-report retry repeats the precondition failure until those multiverses are advanced or explicitly made reportable. The local harness now includes \texttt{{resume-failures}}, fresh idempotency keys, transient backoff, preserved \texttt{{resume\_*}} command records, model-audit refresh, and a fail-fast \texttt{{transient\_failure\_cap\_reached}} status for provider outages.

\section*{{Incomplete Run Details}}
\begin{{longtable}}{{llll}}
\toprule
Event & Status & Failed Tick & Active Multiverses \\
\midrule
{failure_table_rows}
\bottomrule
\end{{longtable}}

\section*{{Variant Breakdown}}
\begin{{longtable}}{{lrrrr}}
\toprule
Variant & Count & Match & Mean TVD & Actual Prob. \\
\midrule
{rows(summary.get('by_variant', {}), 'variant')}
\bottomrule
\end{{longtable}}

\section*{{Category Breakdown}}
\begin{{longtable}}{{lrrrr}}
\toprule
Category & Count & Match & Mean TVD & Actual Prob. \\
\midrule
{rows(summary.get('by_category', {}), 'category')}
\bottomrule
\end{{longtable}}

\section*{{Horizon Breakdown}}
\begin{{longtable}}{{lrrrr}}
\toprule
Horizon & Count & Match & Mean TVD & Actual Prob. \\
\midrule
{rows(summary.get('by_horizon', {}), 'horizon')}
\bottomrule
\end{{longtable}}

\section*{{Findings and Advice}}
The strongest product direction is to make endpoint assessment explicit as durable state, not additional prompt wording. Endpoint guidance and outcome conclusions already exist in prompts/reports; the novel gaps are endpoint coverage ledgers, process-vs-terminal state gates, authority-weighted endpoint evidence, contradiction checks over live alternatives, and outcome-label-aware scoring. For robust evaluation, \texttt{{short\_high\_detail\_default}} was the best completed sweep setting by reliability-adjusted signal, while \texttt{{long\_high\_detail\_strict}} should be reserved for smaller pilots with retry and branch-drain safeguards.

\section*{{Reproducibility}}
The benchmark catalog, hidden source files, raw command outputs, model audits, score summaries, Markdown reports, this \LaTeX{{}} source, and the compiled PDF are retained locally. No generated file is deleted by this harness.

\end{{document}}
"""
    tex_path = LATEX_DIR / "accuracy-evaluation.tex"
    pdf_path = LATEX_DIR / "accuracy-evaluation.pdf"
    tex_path.write_text(tex, encoding="utf-8")
    compiler = shutil.which("pdflatex")
    tectonic = shutil.which("tectonic")
    if not compiler and not tectonic:
        append_log("LaTeX Build", ["Wrote LaTeX source but no LaTeX engine (`pdflatex` or `tectonic`) was found; PDF not built."])
        if args.require_pdf:
            raise SystemExit("no LaTeX engine found")
        return
    if compiler:
        command = [compiler, "-interaction=nonstopmode", "-halt-on-error", "-output-directory", str(LATEX_DIR), str(tex_path)]
    else:
        command = [tectonic, "-o", str(LATEX_DIR), str(tex_path)]
    code, stdout, stderr, elapsed = run_command(command, timeout=120)
    save_command_record(RUNS_DIR / "latex_build.json", command, code, stdout, stderr, elapsed)
    append_log(
        "LaTeX Build",
        [
            f"Wrote `{tex_path.relative_to(STUDY)}`.",
            f"PDF build exit code: {code}.",
            f"Expected PDF path: `{pdf_path.relative_to(STUDY)}`.",
        ],
    )
    if code != 0:
        raise SystemExit("pdflatex failed; see runs/latex_build.json")


def verify_sources(args: argparse.Namespace) -> None:
    results = []
    for event in EVENTS:
        for source_item in event["sources"]:
            url = source_item["url"]
            status = "unchecked"
            code: int | None = None
            error = ""
            try:
                request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "WorldForkAccuracyHarness/1.0"})
                with urllib.request.urlopen(request, timeout=args.timeout) as response:
                    code = response.status
                    status = "ok" if 200 <= code < 400 else "bad_status"
            except urllib.error.HTTPError as exc:
                code = exc.code
                if exc.code in {403, 405}:
                    try:
                        request = urllib.request.Request(url, method="GET", headers={"User-Agent": "WorldForkAccuracyHarness/1.0"})
                        with urllib.request.urlopen(request, timeout=args.timeout) as response:
                            code = response.status
                            status = "ok" if 200 <= code < 400 else "bad_status"
                    except Exception as inner:
                        status = "error"
                        error = str(inner)
                else:
                    status = "bad_status"
                    error = str(exc)
            except Exception as exc:
                status = "error"
                error = str(exc)
            results.append({"event_id": event["id"], "url": url, "status": status, "code": code, "error": error})
    write_json(ANALYSIS_DIR / "source_link_check.json", results)
    failures = [row for row in results if row["status"] != "ok"]
    append_log(
        "Source Link Check",
        [
            f"Checked source URLs: {len(results)}.",
            f"Reachable or accepted: {len(results) - len(failures)}.",
            f"Failures requiring manual review: {len(failures)}.",
            "Details written to `analysis/source_link_check.json`.",
        ],
    )
    if failures and args.fail_on_error:
        raise SystemExit(f"{len(failures)} source links failed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("generate-benchmark").set_defaults(func=generate_benchmark)
    sub.add_parser("validate-benchmark").set_defaults(func=validate_benchmark)
    p = sub.add_parser("verify-sources")
    p.add_argument("--timeout", type=float, default=10)
    p.add_argument("--fail-on-error", action="store_true")
    p.set_defaults(func=verify_sources)
    p = sub.add_parser("health")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--timeout", type=int, default=240)
    p.set_defaults(func=run_health)
    p = sub.add_parser("run-baseline")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--timeout", type=int, default=240)
    p.add_argument("--limit", type=int)
    p.set_defaults(func=run_baseline)
    p = sub.add_parser("run-sweep")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--timeout", type=int, default=240)
    p.add_argument("--limit", type=int)
    p.set_defaults(func=run_sweep)
    p = sub.add_parser("resume-failures", help="Resume failed saved run_record.json entries.")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL, help="WorldFork API base URL.")
    p.add_argument("--timeout", type=int, default=240, help="CLI request timeout in seconds.")
    p.add_argument("--max-requests-per-run", type=int, default=40, help="Cap simulate-next-tick requests, including retries, per failed run.")
    p.add_argument("--retry-attempts", type=int, default=3, help="Attempts per transient simulate-next-tick failure.")
    p.add_argument("--retry-sleep-seconds", type=float, default=10.0, help="Base sleep for bounded transient retry backoff.")
    p.add_argument("--max-consecutive-transient-failures", type=int, default=6, help="Stop a run after this many consecutive transient tick failures.")
    p.add_argument("--only", metavar="EVENT_ID", help="Resume only one failed event id.")
    p.set_defaults(func=resume_failed_runs)
    sub.add_parser("aggregate").set_defaults(func=aggregate_scores)
    p = sub.add_parser("build-latex")
    p.add_argument("--require-pdf", action="store_true")
    p.set_defaults(func=build_latex)
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
