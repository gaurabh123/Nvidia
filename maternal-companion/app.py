import streamlit as st
import pandas as pd
import numpy as np
import folium
from geopy.distance import geodesic
from pathlib import Path

from backend.twilio_service import (
    TwilioConfigError,
    initiate_call,
    send_sms,
)

st.set_page_config(page_title="Mother's Friend - MVP", layout="wide")

# ---------- Data ----------
DATA_DIR = Path("data")
MOTHERS_CSV = DATA_DIR / "mothers.csv"
CHWS_CSV = DATA_DIR / "chws.csv"


@st.cache_data
def load_data():
    mothers = pd.read_csv(MOTHERS_CSV)
    chws = pd.read_csv(CHWS_CSV)
    return mothers, chws


mothers, chws = load_data()

# Persist last generated plan between runs
if "route_plan" not in st.session_state:
    st.session_state["route_plan"] = None

# ---------- Triage ----------
EMERGENCY_FLAGS = {"PPH", "FEVER_HIGH", "PREECLAMPSIA", "SEPSIS", "NB_FEED_ISSUE"}


def triage_assess(row: pd.Series):
    flags = []
    # heavy bleeding -> postpartum hemorrhage
    if str(row.get("bleeding", "none")).lower() == "heavy":
        flags.append("PPH")
    # fever
    temp = float(row.get("temp_c", 0) or 0)
    if temp >= 38.0:
        flags.append("FEVER_HIGH")
    # preeclampsia signs
    if bool(row.get("headache", False)) and bool(row.get("vision_blur", False)):
        flags.append("PREECLAMPSIA")
    # newborn feeding issue
    if str(row.get("baby_feeding", "yes")).lower() == "no":
        flags.append("NB_FEED_ISSUE")

    if any(f in EMERGENCY_FLAGS for f in flags):
        return {"risk": "EMERGENCY", "flags": flags, "sla_hours": 4}
    if flags:
        return {"risk": "PRIORITY", "flags": flags, "sla_hours": 24}
    return {"risk": "ROUTINE", "flags": flags, "sla_hours": 72}


def apply_triage(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    risks, flags, sla = [], [], []
    for _, r in out.iterrows():
        t = triage_assess(r)
        risks.append(t["risk"])
        flags.append(",".join(t["flags"]))
        sla.append(t["sla_hours"])
    out["risk"] = risks
    out["flags"] = flags
    out["sla_hours"] = sla
    # allow manual override via 'priority' column if not 'auto'
    out["priority_final"] = np.where(
        out["priority"].astype(str).eq("auto"), out["risk"], out["priority"]
    )
    return out


mothers = apply_triage(mothers)


# ---------- Distance & Routing ----------
def pairwise_distance(a, b):
    """Geodesic distance in km."""
    return geodesic((a[0], a[1]), (b[0], b[1])).km


def distance_matrix(nodes):
    n = len(nodes)
    D = np.zeros((n, n), dtype=float)
    for i in range(n):
        for j in range(n):
            if i == j:
                D[i, j] = 0.0
            else:
                D[i, j] = pairwise_distance(nodes[i], nodes[j])
    return D


def greedy_assign_routes(mothers_df, chws_df, blocked_edges=None, capacity_per_chw=6):
    """
    Very simple VRP-ish greedy:
    - Sort mothers by priority (EMERGENCY -> PRIORITY -> ROUTINE), then by days_postpartum desc.
    - For each CHW, always go to nearest unvisited mother (respect capacity).
    - blocked_edges: list of tuples [(nodeA_id, nodeB_id)] to penalize with huge distance.
    """
    # Prepare nodes: depot per CHW + mothers
    # For simplicity, we treat each CHW base as its own "depot" start.
    mothers_list = mothers_df[["id", "lat", "lng", "priority_final"]].to_dict("records")
    node_index = {}
    idx = 0
    # Build D dynamically per CHW start
    routes = []
    unserved = set([m["id"] for m in mothers_list])

    # Priority mapping
    prio_rank = {"EMERGENCY": 0, "PRIORITY": 1, "ROUTINE": 2}
    mothers_sorted = sorted(
        mothers_list,
        key=lambda m: (prio_rank.get(str(m["priority_final"]), 3), -1),  # tie handled later
    )

    # Helper to apply blocked edges
    blocked = set()
    for e in blocked_edges or []:
        blocked.add(tuple(sorted(e)))

    def is_blocked(a, b):
        return tuple(sorted((a, b))) in blocked

    for _, chw in chws_df.iterrows():
        start = (float(chw.base_lat), float(chw.base_lng))
        current = ("DEPOT", start)
        cap = int(chw.max_visits_day)
        seq = ["DEPOT"]
        dist = 0.0

        # local list so each CHW picks nearest next
        local_unserved = [m for m in mothers_sorted if m["id"] in unserved]
        while cap > 0 and local_unserved:
            # choose nearest by geodesic from 'current'
            best = None
            best_d = None
            for m in local_unserved:
                d = pairwise_distance(current[1], (m["lat"], m["lng"]))
                # penalize blocked (huge distance)
                if is_blocked(current[0], m["id"]):
                    d += 1e6
                if best is None or d < best_d:
                    best = m
                    best_d = d
            if best is None or best_d > 1e5:  # effectively blocked path
                break
            seq.append(best["id"])
            dist += best_d
            current = (best["id"], (best["lat"], best["lng"]))
            unserved.discard(best["id"])
            local_unserved = [m for m in mothers_sorted if m["id"] in unserved]
            cap -= 1

        routes.append({
            "vehicle_id": chw["id"],
            "chw_name": chw["name"],
            "sequence": seq,
            "km": round(dist, 2),
            "capacity": int(chw.max_visits_day)
        })

    return {"routes": routes, "unserved": sorted(list(unserved))}


# ---------- Visualization ----------
def make_map(mothers_df, chws_df, route_plan):
    # Center map roughly at mean of all points
    all_lats = list(mothers_df["lat"]) + list(chws_df["base_lat"])
    all_lngs = list(mothers_df["lng"]) + list(chws_df["base_lng"])
    center = (float(np.mean(all_lats)), float(np.mean(all_lngs)))
    m = folium.Map(location=center, zoom_start=13)

    # Mothers (colored by priority)
    color_for = {"EMERGENCY": "red", "PRIORITY": "orange", "ROUTINE": "green"}
    for _, r in mothers_df.iterrows():
        folium.CircleMarker(
            location=(r.lat, r.lng),
            radius=6,
            color=color_for.get(r.priority_final, "blue"),
            fill=True,
            fill_opacity=0.9,
            popup=f"{r.id} - {r.priority_final}"
        ).add_to(m)

    # CHW depots
    for _, c in chws_df.iterrows():
        folium.Marker(
            location=(c.base_lat, c.base_lng),
            icon=folium.Icon(color="blue", icon="medkit", prefix="fa"),
            popup=f"{c.id} - {c.name}"
        ).add_to(m)

    # Routes
    id_to_loc = {f"DEPOT:{row.id}": (row.base_lat, row.base_lng) for _, row in chws_df.iterrows()}
    mother_loc = {r.id: (r.lat, r.lng) for _, r in mothers_df.iterrows()}

    for r in route_plan["routes"]:
        coords = []
        # First node is depot; map it to this CHW's depot
        chw = chws_df[chws_df["id"] == r["vehicle_id"]].iloc[0]
        coords.append((chw.base_lat, chw.base_lng))
        for nid in r["sequence"][1:]:
            coords.append(mother_loc[nid])
        folium.PolyLine(coords, weight=4, opacity=0.8).add_to(m)

    return m


# ---------- UI ----------
st.title("Mother's Friend - Postnatal Companion (MVP)")
st.caption("Offline-first triage + CHW routing + what-if vehicles")

left, right = st.columns([2, 1])

with right:
    st.subheader("What-if Controls")
    vehicle_boost = st.slider("Extra CHWs (simulate)", 0, 3, 0)
    capacity = st.slider("Visits per CHW per day", 3, 12, 6)
    st.markdown("**Blocked edge** (simulate road closure)")
    block_from = st.selectbox("From (mother id)", ["none"] + mothers["id"].tolist())
    block_to = st.selectbox("To (mother id)", ["none"] + mothers["id"].tolist())

    apply_btn = st.button("Plan Routes", use_container_width=True)

    st.markdown("---")
    st.subheader("Twilio Notifications")
    st.caption("Requires TWILIO_* environment variables with valid Twilio credentials.")

    with st.form("sms_form"):
        sms_to = st.text_input("SMS recipient (E.164)")
        sms_body = st.text_area("SMS message", height=120)
        sms_from = st.text_input("Override from number (optional)")
        sms_service = st.text_input("Override Messaging Service SID (optional)")
        sms_submit = st.form_submit_button("Send SMS")

        if sms_submit:
            if not sms_to or not sms_body:
                st.warning("Provide both recipient and message body to send an SMS.")
            else:
                try:
                    sid = send_sms(
                        to=sms_to,
                        body=sms_body,
                        from_number=sms_from or None,
                        messaging_service_sid=sms_service or None,
                    )
                    st.success(f"SMS queued (sid: {sid})")
                except TwilioConfigError as exc:
                    st.error(f"Twilio configuration error: {exc}")
                except RuntimeError as exc:
                    st.error(str(exc))

    with st.form("voice_form"):
        voice_to = st.text_input("Voice call recipient (E.164)")
        voice_from = st.text_input("Override caller ID (optional)")
        voice_twiml_url = st.text_input("TwiML URL (optional)")
        voice_twiml = st.text_area("Inline TwiML (optional)", height=120)
        voice_submit = st.form_submit_button("Start Voice Call")

        if voice_submit:
            if not voice_to:
                st.warning("Provide a recipient number to start a voice call.")
            else:
                try:
                    sid = initiate_call(
                        to=voice_to,
                        from_number=voice_from or None,
                        twiml_url=voice_twiml_url or None,
                        twiml=voice_twiml or None,
                    )
                    st.success(f"Voice call queued (sid: {sid})")
                except TwilioConfigError as exc:
                    st.error(f"Twilio configuration error: {exc}")
                except RuntimeError as exc:
                    st.error(str(exc))

with left:
    st.subheader("Triage Summary")
    st.dataframe(
        mothers[["id", "name", "days_postpartum", "risk", "flags", "sla_hours", "priority_final"]],
        use_container_width=True
    )

if apply_btn:
    # Expand CHW fleet
    chws_sim = chws.copy()
    if vehicle_boost > 0:
        for k in range(vehicle_boost):
            base = chws.iloc[0]
            chws_sim.loc[len(chws_sim)] = {
                "id": f"chwX{k+1}",
                "name": f"Temp CHW {k+1}",
                "phone": "000",
                "base_lat": base.base_lat,
                "base_lng": base.base_lng,
                "max_visits_day": capacity,
                "transport": base.transport,
            }
    else:
        chws_sim["max_visits_day"] = capacity

    blocked = []
    if block_from != "none" and block_to != "none" and block_from != block_to:
        blocked = [(block_from, block_to)]

    plan = greedy_assign_routes(mothers, chws_sim, blocked_edges=blocked, capacity_per_chw=capacity)
    st.session_state["route_plan"] = plan
    st.session_state["route_plan_chws"] = chws_sim

plan = st.session_state.get("route_plan")
chws_for_plan = st.session_state.get("route_plan_chws", chws)

if plan:
    st.subheader("Route Plan")
    st.write(plan)

    fmap = make_map(mothers, chws_for_plan, plan)
    st.components.v1.html(fmap._repr_html_(), height=520, scrolling=True)

st.info("Tip: change fever/bleeding/headache in `data/mothers.csv` to see triage and routing react.")
