from __future__ import annotations

from copy import deepcopy
from typing import Any

SCENARIO_FORMAT = [
    "Scenario title",
    "Simulation duration",
    "Tick size",
    "Initial public event",
    "Main actors/heroes",
    "Initial cohorts",
    "Public channels",
    "Branch triggers",
    "Expected reports/questions",
]

SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "viral_misconduct_video",
        "title": "Metro City viral misconduct video",
        "duration": "4 weeks",
        "tick_size": "12 hours",
        "category": "viral_outage_and_mobilization",
        "fictionalization": "fictionalized_but_realistic",
        "initial_public_event": "A video alleging police misconduct spreads across OASIS and local media before official facts are clear.",
        "main_actors": ["Metro Police Office", "City Hall Response Team", "Community Legal Collective", "Local Reporter Vale"],
        "initial_cohorts": ["outraged residents", "institutional-trust residents", "public-safety voters", "silent majority", "counter-protest bloc"],
        "public_channels": ["OASIS", "local broadcast", "campus/community forums"],
        "branch_triggers": ["early apology", "official denial", "second video", "outside influencer amplification", "protest splintering", "counter-movement formation"],
        "expected_reports_questions": ["How did trust collapse or recover?", "Which cohorts crossed mobilization thresholds?", "Where did silence pressure alter visible opinion?"],
        "tests": ["outrage", "trust_collapse", "protests", "misinformation", "god_branching"],
    },
    {
        "id": "campus_speaker_cancellation",
        "title": "University B speaker cancellation crisis",
        "duration": "2 months",
        "tick_size": "1 day",
        "category": "campus_faction_splits",
        "fictionalization": "fictionalized_but_realistic",
        "initial_public_event": "University B abruptly cancels a controversial speaker, triggering student, faculty, donor, and media backlash.",
        "main_actors": ["University B Administration", "Student Free Speech Coalition", "Student Safety Coalition", "Faculty Senate", "Alumni Donor Circle"],
        "initial_cohorts": ["silent majority students", "activist students", "skeptical students", "faculty", "parents", "alumni donors"],
        "public_channels": ["OASIS", "campus newspaper", "faculty listserv", "donor letters"],
        "branch_triggers": ["administration negotiates", "protest discipline", "faculty senate intervention", "donor funding threat", "national media reframing"],
        "expected_reports_questions": ["Which faction splits became durable?", "Did elite influence overpower student graph pressure?", "Did merge paths emerge around process legitimacy?"],
        "tests": ["cohort_split", "merge_logic", "elite_media", "identity_salience"],
    },
    {
        "id": "public_health_advisory_cascade",
        "title": "Metro County disease-cluster advisory",
        "duration": "6 weeks",
        "tick_size": "12 hours",
        "category": "public_health_misinformation",
        "fictionalization": "fictionalized_but_realistic",
        "initial_public_event": "Metro County issues a public-health advisory after a disease cluster, but details are incomplete and rumors spread.",
        "main_actors": ["County Health Office", "Hospital Network", "Independent Expert Dr. Ren", "Local Wellness Influencer"],
        "initial_cohorts": ["risk-averse families", "low-trust residents", "healthcare workers", "small businesses", "confused observers"],
        "public_channels": ["OASIS", "public-health briefings", "local radio", "school messages"],
        "branch_triggers": ["clear trusted advisory", "delayed advisory", "contradictory expert statement", "false influencer claim", "platform suppression backlash"],
        "expected_reports_questions": ["Which correction mechanisms worked?", "How did fear and confusion change compliance?", "How did trust networks rewire?"],
        "tests": ["fear", "confusion", "institutional_trust", "complex_contagion", "correction_mechanisms"],
    },
    {
        "id": "teachers_strike_dependency",
        "title": "Metro School teachers strike threat",
        "duration": "3 months",
        "tick_size": "1 day",
        "category": "labor_dependency",
        "fictionalization": "fictionalized_but_realistic",
        "initial_public_event": "A teachers union threatens a strike during contract negotiations, forcing parents, administrators, and city officials into dependency conflict.",
        "main_actors": ["Teachers Union", "School District Office", "Parent Logistics Network", "Mayor A", "Student Organizer Nia"],
        "initial_cohorts": ["pro-union parents", "disruption-averse parents", "teachers", "students", "city officials"],
        "public_channels": ["OASIS", "school board meetings", "parent chats", "local news"],
        "branch_triggers": ["public supports workers", "public turns against disruption", "government intervention", "union internal split", "negotiation leak"],
        "expected_reports_questions": ["How did dependency alter public support?", "When did mobilization cross threshold?", "Did the union split or merge with parent groups?"],
        "tests": ["dependency_graph", "labor_mobilization", "parent_cohort_split", "policy_feedback"],
    },
    {
        "id": "ai_product_scandal",
        "title": "CivicAI product scandal and regulator cascade",
        "duration": "4 months",
        "tick_size": "2 days",
        "category": "technology_trust_regulation",
        "fictionalization": "fictionalized_but_realistic",
        "initial_public_event": "CivicAI releases an AI product that makes a highly visible error affecting benefits, school placement, or public-safety decisions.",
        "main_actors": ["CivicAI Company", "Affected Users Network", "Tech Safety Journalist", "State Regulator", "Competitor Platform"],
        "initial_cohorts": ["tech optimists", "affected families", "safety advocates", "industry defenders", "regulatory skeptics"],
        "public_channels": ["OASIS", "tech press", "regulatory hearing", "creator videos"],
        "branch_triggers": ["company pauses product", "company doubles down", "whistleblower appears", "regulator opens investigation", "competitors exploit scandal"],
        "expected_reports_questions": ["Which trust edges collapsed?", "Did ideology axes predict support?", "How did affected users organize pressure?"],
        "tests": ["algorithmic_trust", "regulatory_cascade", "whistleblower_branch", "media_amplification"],
    },
    {
        "id": "housing_emergency_zoning",
        "title": "Governor A housing emergency and zoning reform",
        "duration": "3 months",
        "tick_size": "2 days",
        "category": "policy_backlash",
        "fictionalization": "fictionalized_but_realistic",
        "initial_public_event": "Governor A declares a housing emergency and proposes zoning reform that pits renters, homeowners, builders, and local officials against one another.",
        "main_actors": ["Governor A", "Tenant Union", "Homeowner Alliance", "Builder Association", "City Planning Board"],
        "initial_cohorts": ["renters", "homeowners", "suburban parents", "builders", "local-control advocates"],
        "public_channels": ["OASIS", "town halls", "local news", "policy newsletters"],
        "branch_triggers": ["rent spike data appears", "homeowner lawsuit", "builder lobbying leak", "governor compromise", "city defiance"],
        "expected_reports_questions": ["How did economic dependency shape ideology?", "Which coalitions formed across usual lines?", "Did attention decay before policy vote?"],
        "tests": ["homeowner_renter_conflict", "coalition_formation", "attention_decay", "policy_legitimacy"],
    },
    {
        "id": "creator_algorithm_shock",
        "title": "Platform X algorithm shock hits small creators",
        "duration": "2 weeks",
        "tick_size": "12 hours",
        "category": "platform_dynamics",
        "fictionalization": "fictionalized_but_realistic",
        "initial_public_event": "Platform X changes its algorithm and small creators suddenly lose reach, income, and trust in platform governance.",
        "main_actors": ["Platform X Trust Desk", "Small Creator Coalition", "Large Creator Lia", "Advertiser Council"],
        "initial_cohorts": ["small creators", "large creators", "fans", "advertisers", "platform employees"],
        "public_channels": ["Platform X", "OASIS", "creator newsletters", "advertiser forums"],
        "branch_triggers": ["platform explains change", "platform stays silent", "creator boycott", "large creator defects", "advertisers pressure platform"],
        "expected_reports_questions": ["How did exposure shocks rewire influence?", "Did creator coalitions become durable?", "Did platform trust recover?"],
        "tests": ["exposure_graph", "creator_coalition", "platform_trust", "social_graph_shock"],
    },
    {
        "id": "regional_water_shortage",
        "title": "Regional water shortage rationing crisis",
        "duration": "6 months",
        "tick_size": "1 week",
        "category": "resource_dependency",
        "fictionalization": "fictionalized_but_realistic",
        "initial_public_event": "A regional water shortage forces rationing decisions across farms, suburbs, industry, and urban neighborhoods.",
        "main_actors": ["Regional Water Authority", "Farmers Alliance", "Suburban Homeowners", "Urban Tenant Network", "Industrial Users Council"],
        "initial_cohorts": ["farm communities", "suburban households", "urban renters", "industrial workers", "environment advocates"],
        "public_channels": ["OASIS", "water authority briefings", "local newspapers", "town halls"],
        "branch_triggers": ["rationing perceived as fair", "leak of favored exemptions", "severe weather shock", "lawsuit", "black-market water story"],
        "expected_reports_questions": ["How did fairness narratives affect compliance?", "Which dependency edges dominated?", "Did regional identity fracture?"],
        "tests": ["resource_dependency", "fairness_narratives", "compliance", "long_horizon_branching"],
    },
    {
        "id": "mayoral_election_after_crisis",
        "title": "Metro City mayoral election after public-safety crisis",
        "duration": "9 months",
        "tick_size": "1 week",
        "category": "election_legitimacy_and_trust",
        "fictionalization": "fictionalized_but_realistic",
        "initial_public_event": "A public-safety crisis reshapes a mayoral election, with candidates competing to define competence, fairness, and blame.",
        "main_actors": ["Incumbent Mayor A", "Challenger B", "Election Office", "Public Safety Union", "Neighborhood Coalition"],
        "initial_cohorts": ["safety-first voters", "civil-liberties voters", "low-trust voters", "business owners", "young voters"],
        "public_channels": ["OASIS", "debates", "local press", "campaign ads", "community meetings"],
        "branch_triggers": ["competence recovery", "partisan rumor", "court rejects claim", "local media debunks claim", "recount pressure", "protest after result"],
        "expected_reports_questions": ["How did crisis causality affect voting blocs?", "Which rumors survived debunking?", "How did coalition shifts affect final legitimacy?"],
        "tests": ["campaign_dynamics", "rumor_containment", "trust", "final_report_quality"],
    },
    {
        "id": "public_ai_deployment",
        "title": "Public AI deployment in welfare decisions",
        "duration": "12 months",
        "tick_size": "2 weeks",
        "category": "algorithmic_public_institution",
        "fictionalization": "fictionalized_but_realistic",
        "initial_public_event": "A public agency deploys an AI decision system for welfare eligibility, producing early complaints about errors and opaque appeals.",
        "main_actors": ["Public Benefits Agency", "Affected Claimants Network", "Civil Liberties Clinic", "AI Vendor", "Oversight Committee"],
        "initial_cohorts": ["affected claimants", "agency staff", "tech optimists", "privacy advocates", "taxpayer watchdogs"],
        "public_channels": ["OASIS", "agency portal notices", "legal clinic briefings", "oversight hearings"],
        "branch_triggers": ["error scandal", "vendor transparency pledge", "regulator audit", "lawsuit-like pressure", "agency rollback", "model expansion"],
        "expected_reports_questions": ["How did algorithmic trust evolve?", "Where did affected cohorts gain influence?", "Did regulators adapt or lag?"],
        "tests": ["algorithmic_trust", "affected_cohort_mobilization", "regulators", "long_attention_cycle"],
    },
]

COVERAGE_MATRIX = {
    "viral_outage": ["viral_misconduct_video", "ai_product_scandal"],
    "institutional_trust_collapse": ["viral_misconduct_video", "public_ai_deployment"],
    "protest_mobilization": ["viral_misconduct_video", "campus_speaker_cancellation"],
    "labor_conflict": ["teachers_strike_dependency"],
    "public_health_or_safety_crisis": ["public_health_advisory_cascade", "regional_water_shortage"],
    "policy_backlash": ["housing_emergency_zoning", "public_ai_deployment"],
    "technology_controversy": ["ai_product_scandal", "public_ai_deployment"],
    "platform_social_media_dynamics": ["creator_algorithm_shock"],
    "economic_dependency": ["teachers_strike_dependency", "regional_water_shortage", "housing_emergency_zoning"],
    "election_legitimacy": ["mayoral_election_after_crisis"],
    "long_term_attention_decay": ["regional_water_shortage", "mayoral_election_after_crisis", "public_ai_deployment"],
    "cohort_split_merge_testing": ["campus_speaker_cancellation", "teachers_strike_dependency"],
    "hero_agent_influence": ["viral_misconduct_video", "ai_product_scandal", "teachers_strike_dependency"],
    "final_report_quality": ["mayoral_election_after_crisis", "public_ai_deployment"],
}


def list_scenarios(category: str | None = None, test: str | None = None) -> list[dict[str, Any]]:
    scenarios = deepcopy(SCENARIOS)
    if category:
        scenarios = [item for item in scenarios if item.get("category") == category]
    if test:
        scenarios = [item for item in scenarios if test in item.get("tests", [])]
    return scenarios


def get_scenario(scenario_id: str) -> dict[str, Any] | None:
    for scenario in SCENARIOS:
        if scenario["id"] == scenario_id:
            return deepcopy(scenario)
    return None


def scenario_to_big_bang_payload(scenario_id: str) -> dict[str, Any] | None:
    scenario = get_scenario(scenario_id)
    if not scenario:
        return None
    scenario_text = _scenario_text(scenario)
    return {
        "name": scenario["title"],
        "description": f"Scenario-bank simulation: {scenario['category']}",
        "scenario_text": scenario_text,
        "scenario_input": {
            "premise": scenario["initial_public_event"],
            "setting": "fictionalized realistic public-event simulation",
            "scenario_bank_id": scenario_id,
            "fictionalization": scenario["fictionalization"],
        },
        "simulation_config": {"tick_duration": scenario["tick_size"], "max_ticks": _duration_to_ticks(scenario["duration"], scenario["tick_size"])},
        "branch_policy": {"max_branch_depth": 3, "max_active_multiverses": 12, "max_branches_per_tick": 2, "branch_score_threshold": 0.7},
        "actors": [{"name": name, "actor_type": _actor_type(name)} for name in scenario["main_actors"]],
        "cohorts": [{"name": name} for name in scenario["initial_cohorts"]],
        "heroes": [{"name": name, "actor_type": "hero"} for name in scenario["main_actors"] if _actor_type(name) == "hero"],
        "use_initializer_agent": True,
    }


def _scenario_text(scenario: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"Scenario title: {scenario['title']}",
            f"Simulation duration: {scenario['duration']}",
            f"Tick size: {scenario['tick_size']}",
            f"Initial public event: {scenario['initial_public_event']}",
            f"Main actors/heroes: {', '.join(scenario['main_actors'])}",
            f"Initial cohorts: {', '.join(scenario['initial_cohorts'])}",
            f"Public channels: {', '.join(scenario['public_channels'])}",
            f"Branch triggers: {', '.join(scenario['branch_triggers'])}",
            f"Expected reports/questions: {'; '.join(scenario['expected_reports_questions'])}",
            "Treat this as fictionalized public-event dynamics. Do not target real people or produce persuasion instructions.",
        ]
    )


def _actor_type(name: str) -> str:
    lowered = name.lower()
    if any(token in lowered for token in ["reporter", "organizer", "dr.", "nia", "lia", "vale"]):
        return "hero"
    if any(token in lowered for token in ["office", "authority", "agency", "city", "university", "district", "committee", "company", "platform"]):
        return "institution"
    return "cohort"


def _duration_to_ticks(duration: str, tick_size: str) -> int:
    duration = duration.lower()
    tick_size = tick_size.lower()
    if "12 month" in duration:
        base_days = 365
    elif "9 month" in duration:
        base_days = 270
    elif "6 month" in duration:
        base_days = 180
    elif "4 month" in duration:
        base_days = 120
    elif "3 month" in duration:
        base_days = 90
    elif "2 month" in duration:
        base_days = 60
    elif "6 week" in duration:
        base_days = 42
    elif "4 week" in duration:
        base_days = 28
    elif "3 month" in duration:
        base_days = 90
    elif "2 week" in duration:
        base_days = 14
    else:
        base_days = 30
    if "12 hour" in tick_size:
        return min(180, base_days * 2)
    if "2 week" in tick_size:
        return max(1, base_days // 14)
    if "1 week" in tick_size:
        return max(1, base_days // 7)
    if "2 day" in tick_size:
        return max(1, base_days // 2)
    return min(180, base_days)
