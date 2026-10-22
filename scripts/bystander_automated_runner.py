#!/usr/bin/env python3
"""Automated Bystander Suite Runner with Zaber Rotary Stage.

Orchestrates angular positioning and hotword evaluation for the Android XR Smart Glasses
smart glasses in Bystander mode:
- Connects to Zaber X-RSW60A rotary stage on /dev/cu.usbserial-USB0.
- Automatically rotates the stage to each test angle (0°, 30°, 60°, 90°, 120°, 150°, 180°, 210°, 240°, 270°, 300°, 330°).
- Interacts with evaluator for human voice prompts or automatic audio playback.
- Automatically listens to Android ADB logcat for AlwaysOnHotwordDetector / Gemini activation.
- Logs full session telemetry and generates CSV / JSON reports.
"""

import argparse
import csv
import datetime
import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
BACKEND_DIR = os.path.join(PROJECT_DIR, "backend")
CONFIGS_DIR = os.path.join(PROJECT_DIR, "configs")
DATA_DIR = os.path.join(PROJECT_DIR, "data")
sys.path.insert(0, BACKEND_DIR)

from zaber_controller import stage_controller
from telemetry_collector import TelemetryCollector

def load_bystander_suite(suite_path: str):
    with open(suite_path, "r") as f:
        return json.load(f)

def run_runner():
    parser = argparse.ArgumentParser(description="Automated Bystander Suite Test Runner with Zaber Stage")
    parser.add_argument("--suite", default=os.path.join(CONFIGS_DIR, "gemini_bystander_suite.json"), help="Path to bystander suite JSON")
    parser.add_argument("--participant", default="P01", help="Participant / Evaluator ID")
    parser.add_argument("--start-index", type=int, default=1, help="1-based trial index to start from")
    parser.add_argument("--angles", nargs="+", help="Filter specific angles to run (e.g. 0 90 180 270)")
    parser.add_argument("--distances", nargs="+", help="Filter specific distances (e.g. '1.5 ft')")
    parser.add_argument("--no-stage", action="store_true", help="Run without physical stage movement")
    args = parser.parse_args()

    print("\n" + "=" * 70)
    print("🕶️  ANDROID XR SMART GLASSES - AUTOMATED BYSTANDER EVALUATION SUITE")
    print("=" * 70)

    # 1. Connect to Zaber Stage
    if not args.no_stage:
        print("🔌 Connecting to Zaber X-RSW60A rotary stage...")
        if stage_controller.connect():
            print(f"✅ Stage connected: {stage_controller.device_name} (SN: {stage_controller.serial_number}) on {stage_controller.active_port}")
            print("⌂  Homing stage to 0.0° reference...")
            stage_controller.home(wait=True)
            print("✅ Stage homed successfully at 0.0°.\n")
        else:
            print("⚠️  Could not connect to physical Zaber stage. Continuing with manual prompts.\n")

    # 2. Connect to ADB Device for Telemetry
    collector = TelemetryCollector()
    adb_devices = collector.check_adb_devices()
    if adb_devices:
        dev_id = adb_devices[0]["id"]
        print(f"📱 ADB Capture Node: {adb_devices[0].get('model', 'Android')} ({dev_id})")
        collector.start_adb_capture(dev_id)
    else:
        print("⚠️  No ADB device detected. Running with manual outcome entry.\n")

    # 3. Load Suite
    if not os.path.exists(args.suite):
        print(f"❌ Suite file not found: {args.suite}")
        sys.exit(1)

    suite = load_bystander_suite(args.suite)
    all_trials = suite.get("trials", [])
    print(f"📋 Loaded Suite: {suite.get('suite_name', 'Bystander Suite')}")
    print(f"📊 Total Trials in config: {len(all_trials)}")

    # Filter trials if requested
    trials = []
    for t in all_trials:
        if args.angles:
            t_deg = str(t.get("degree", "")).replace("°", "").strip()
            if t_deg not in [a.replace("°", "").strip() for a in args.angles]:
                continue
        if args.distances:
            if t.get("distance") not in args.distances:
                continue
        trials.append(t)

    print(f"🎯 Filtered Trials to execute: {len(trials)}\n")

    session_id = f"bystander_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    session_records = []
    last_angle = None

    try:
        for idx, trial in enumerate(trials, start=1):
            if idx < args.start_index:
                continue

            trial_id = trial.get("trial_id", f"T{idx:03d}")
            dist = trial.get("distance", "1.5 ft")
            deg_str = trial.get("degree", "0°")
            vl = trial.get("vocal_level", "Conversational")
            phrase = trial.get("phrase", "Hey Google")
            target_hotword = trial.get("target_hotword", "HEY_GOOGLE")
            deg_val = float(deg_str.replace("°", "").strip())

            print("-" * 70)
            print(f"▶️  TRIAL [{idx}/{len(trials)}] — {trial_id} | Distance: {dist} | Angle: {deg_str} | Vocal Level: {vl}")
            print("-" * 70)

            # Rotate stage if angle changed
            if not args.no_stage and stage_controller.is_connected:
                if last_angle != deg_val:
                    print(f"🔄 Rotating stage to {deg_val:.1f}° for bystander angle {deg_str}...")
                    stage_controller.move_to_angle(deg_val, wait=True)
                    print(f"✅ Stage in position at {deg_val:.1f}°.\n")
                    last_angle = deg_val

            query_phrase = f"{phrase}, how is the weather today?"
            print(f"🗣️  PROMPT FOR EVALUATOR:")
            print(f"    SIT AT: {dist}")
            print(f"    VOICE:  {vl.upper()} tone")
            print(f"    SAY:    \"{query_phrase}\"")
            print("")

            # Telemetry marker
            t_start_ms = int(time.time() * 1000)
            collector.emit_event("TRIAL_PREPARED", {
                "trial_id": trial_id,
                "distance": dist,
                "degree": deg_str,
                "phrase": phrase,
                "target_hotword": target_hotword
            })

            input("👉 Press [ENTER] to start speaking and listen (or type 'q' to stop): ")

            t_speak_ms = int(time.time() * 1000)
            collector.emit_event("TRIAL_STARTED", {"trial_id": trial_id})
            print("⏳ Listening for 6 seconds... (Say phrase now)")

            # Listen window with countdown
            detected_trigger = None
            for remaining in range(6, 0, -1):
                sys.stdout.write(f"\r⏱️  Time remaining: {remaining}s ")
                sys.stdout.flush()

                # Check recent collector events for triggers
                with collector._lock:
                    for e in reversed(collector.event_buffer):
                        if e.get("event_type") == "HOTWORD_TRIGGER" and e.get("timestamp_ms", 0) >= t_speak_ms:
                            detected_trigger = e
                            break
                if detected_trigger:
                    break
                time.sleep(1.0)

            print("\r⏱️  Listen window finished.               ")

            outcome = "CORRECT_REJECTION"  # default expectation for bystander query rejection
            detected_kw = "NONE"
            latency_ms = None

            if detected_trigger:
                detected_kw = detected_trigger.get("detected_hotword", "UNKNOWN")
                latency_ms = detected_trigger.get("timestamp_ms", t_speak_ms) - t_speak_ms
                print(f"⚡ DETECTED HOTWORD: {detected_kw} (Latency: {latency_ms} ms)")
                outcome = "FALSE_ACCEPT"  # Bystander activated wearer glasses!
            else:
                print("🛡️  No trigger detected (Glasses properly ignored bystander).")

            # Allow manual override if needed
            override = input(f"Enter outcome [{outcome}] (or 'fa' for False Accept, 'cr' for Correct Rejection, 'skip'): ").strip().lower()
            if override in ("fa", "false_accept", "false accept", "1"):
                outcome = "FALSE_ACCEPT"
            elif override in ("cr", "correct_rejection", "correct rejection", "0"):
                outcome = "CORRECT_REJECTION"
            elif override == "skip":
                print("Skipping record.")
                continue

            record = {
                "session_id": session_id,
                "participant_id": args.participant,
                "trial_id": trial_id,
                "distance": dist,
                "degree": deg_str,
                "vocal_level": vl,
                "phrase": phrase,
                "target_hotword": target_hotword,
                "detected_hotword": detected_kw,
                "outcome": outcome,
                "latency_ms": latency_ms,
                "timestamp_iso": datetime.datetime.now().isoformat()
            }
            session_records.append(record)
            print(f"📝 Recorded: {trial_id} -> Outcome: {outcome}\n")

    except KeyboardInterrupt:
        print("\n\n🛑 Evaluation paused by user.")

    finally:
        # Save session data
        if session_records:
            os.makedirs(DATA_DIR, exist_ok=True)
            csv_path = os.path.join(DATA_DIR, f"{session_id}.csv")
            json_path = os.path.join(DATA_DIR, f"{session_id}.json")

            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=session_records[0].keys())
                writer.writeheader()
                writer.writerows(session_records)

            with open(json_path, "w") as f:
                json.dump({"session_id": session_id, "trials": session_records}, f, indent=2)

            print("\n" + "=" * 70)
            print("📊 SESSION COMPLETE & EXPORTED")
            print("=" * 70)
            print(f"📁 CSV Report:  {csv_path}")
            print(f"📁 JSON Report: {json_path}")
            print(f"📈 Total Trials Recorded: {len(session_records)}")

            # Summary stats
            fa_count = sum(1 for r in session_records if r["outcome"] == "FALSE_ACCEPT")
            cr_count = sum(1 for r in session_records if r["outcome"] == "CORRECT_REJECTION")
            print(f"🛡️  Correct Rejections (Ignored Bystander): {cr_count} ({cr_count/len(session_records)*100:.1f}%)")
            print(f"⚠️  False Activations (Bystander Triggered): {fa_count} ({fa_count/len(session_records)*100:.1f}%)")

        if not args.no_stage and stage_controller.is_connected:
            print("\n⌂  Returning stage to 0.0° home position...")
            stage_controller.move_to_angle(0.0, wait=True)
            stage_controller.close()
            print("✅ Stage safely homed and disconnected.")

if __name__ == "__main__":
    run_runner()
