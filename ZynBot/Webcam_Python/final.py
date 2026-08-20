import time
import json
import threading
import queue
import os
import platform
import sys
import traceback
import socket

import cv2
import sounddevice as sd
from vosk import Model, KaldiRecognizer

print("TOP OF FILE RAN", flush=True)

# ----------------------------
# CONFIG
# ----------------------------

DROIDCAM_URLS = [
    "http://192.168.4.26:4747/video",
    "http://192.168.4.26:4747/mjpegfeed",
]

VOSK_MODEL_PATH_WINDOWS = r"C:\Users\icicl\Desktop\ZynBot\Webcam_Python\vosk-model-small-en-us-0.15"
VOSK_MODEL_PATH_PI = "/home/pi/vosk-model-small-en-us-0.15"

AUDIO_DEVICE_INDEX_WINDOWS = 14  # Microphone (DroidCam Virtual Audio)

PHRASES = [
    "zyn me", "zen me", "zin me",
    "i want a zyn", "i want a zen", "i want a zin",
    "stop", "cancel"
]
ARM_COMMANDS = {
    "zyn me", "zen me", "zin me",
    "i want a zyn", "i want a zen", "i want a zin"
}
DISARM_COMMANDS = {"stop", "cancel"}

MIN_CONF = 0.65

HEARTBEAT_SECONDS = 2.0
PRINT_AUDIO_DEVICES_ON_START = True
WINDOWS_OUT_PRINT_EVERY = 0.25

MOTOR_SPIN_SECONDS = 3
LOCK_WINDOW_DEG = 5
LOCK_HOLD_SECONDS = 0.35
TRIGGER_PULSE_SECONDS = 0.5
REQUIRE_FACE_TO_FIRE = True
FACE_HOLD_SECONDS = 0.75

PI_HOST = "192.168.4.33"
PI_PORT = 5005
ACK_TIMEOUT_SECONDS = 0.35
SERVO_STEP_DEADBAND_DEG = 1

CAMERA_TRY_URL_FIRST = False
CAMERA_SCAN_MAX_INDEX = 10
PREFERRED_CAMERA_KEYWORDS = ["droidcam"]
USE_MSMF_BACKEND = False

# Aim scaling / alignment
CAMERA_HFOV_DEG = 200
SERVO_CENTER_DEG = 90
SERVO_ALIGN_OFFSET_DEG = -2
SERVO_MIN_DEG = 30
SERVO_MAX_DEG = 135

# ----------------------------
# Helpers
# ----------------------------

def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v

def _backend_flag():
    if platform.system().lower() != "windows":
        return 0
    return cv2.CAP_MSMF if USE_MSMF_BACKEND else cv2.CAP_DSHOW

def _try_read_frame(cap, warmup_reads=3):
    if not cap or not cap.isOpened():
        return False, None
    frame = None
    ok = False
    for _ in range(warmup_reads):
        ok, frame = cap.read()
        if ok and frame is not None:
            break
    return ok, frame

def _frame_quality_score(frame):
    if frame is None:
        return -1
    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    mean = float(gray.mean())
    if mean < 5.0:
        return -1
    return (w * h) + (mean * 10.0)

def _open_url_camera():
    last_err = None
    for url in DROIDCAM_URLS:
        print("[CAM] Trying URL:", url, flush=True)
        cap = cv2.VideoCapture(url)
        print("[CAM] isOpened =", cap.isOpened(), flush=True)
        ok, frame = _try_read_frame(cap)
        if ok and frame is not None:
            print("[CAM] Using URL stream:", url, "First frame:", frame.shape, flush=True)
            return cap, None
        cap.release()
        last_err = f"url failed: {url}"
    return None, last_err

def _list_windows_cameras_pygrabber():
    try:
        from pygrabber.dshow_graph import FilterGraph
        graph = FilterGraph()
        return graph.get_input_devices()
    except Exception:
        return None

def _open_named_camera_if_possible():
    names = _list_windows_cameras_pygrabber()
    if not names:
        return None

    print("[CAM] Detected Windows video devices:", flush=True)
    for i, n in enumerate(names):
        print(f"  [{i}] {n}", flush=True)

    keywords = [k.lower() for k in PREFERRED_CAMERA_KEYWORDS]
    for i, n in enumerate(names):
        low = n.lower()
        if any(k in low for k in keywords):
            print("[CAM] Found preferred device:", n, "at index", i, flush=True)
            cap = cv2.VideoCapture(i, _backend_flag())
            ok, frame = _try_read_frame(cap)
            if ok and frame is not None:
                print("[CAM] Using preferred device index", i, "First frame:", frame.shape, flush=True)
                return cap
            cap.release()
            print("[CAM] Preferred device index opened but no frames. Continuing scan.", flush=True)

    return None

def _scan_indices_best_camera():
    best = None
    best_idx = None
    best_score = -1

    for idx in range(0, CAMERA_SCAN_MAX_INDEX + 1):
        print("[CAM] Trying device index:", idx, "backend:", ("MSMF" if USE_MSMF_BACKEND else "DSHOW"), flush=True)
        cap = cv2.VideoCapture(idx, _backend_flag())
        ok, frame = _try_read_frame(cap)
        if not ok or frame is None:
            cap.release()
            continue

        score = _frame_quality_score(frame)
        print("[CAM]  index", idx, "frame", frame.shape, "score", int(score), flush=True)

        if score > best_score:
            if best is not None:
                try:
                    best.release()
                except Exception:
                    pass
            best = cap
            best_idx = idx
            best_score = score
        else:
            cap.release()

    if best is not None:
        print("[CAM] Selected camera index", best_idx, "with score", int(best_score), flush=True)
        return best

    return None

def open_camera():
    if CAMERA_TRY_URL_FIRST:
        cap, err = _open_url_camera()
        if cap is not None:
            return cap
        print("[CAM] URL mode failed:", err, flush=True)

    if platform.system().lower() == "windows":
        cap = _open_named_camera_if_possible()
        if cap is not None:
            return cap

    cap = _scan_indices_best_camera()
    if cap is not None:
        return cap

    raise RuntimeError(
        "Could not open any camera. "
        "If you are using the DroidCam Windows client, make sure Video is enabled and no other app is using it. "
        "If you are using URL mode, check the phone IP/port."
    )

# ----------------------------
# GPIO abstraction (PC -> Pi)
# ----------------------------

class DummyGPIO:
    def set_servo_deg(self, deg): pass
    def set_mosfet(self, state): pass
    def set_trigger(self, state): pass
    def send_and_wait_ack(self): return True
    def cleanup(self): pass


class NetGPIO:
    """
    Sends {seq, servo_deg, armed, launch} to the Pi over TCP as JSON.
    Waits for an ACK from the Pi before returning success.
    Pi should reply with JSON like:
        {"ok": true, "seq": <same seq>}
    """
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self._seq = 0
        self._state = {"servo_deg": 90.0, "armed": False, "launch": False}

    def set_servo_deg(self, deg):
        self._state["servo_deg"] = float(deg)

    def set_mosfet(self, state):
        self._state["armed"] = bool(state)

    def set_trigger(self, state):
        self._state["launch"] = bool(state)

    def send_and_wait_ack(self):
        self._seq += 1
        payload_dict = {
            "seq": self._seq,
            "servo_deg": self._state["servo_deg"],
            "armed": self._state["armed"],
            "launch": self._state["launch"],
        }
        payload = json.dumps(payload_dict).encode("utf-8")

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(ACK_TIMEOUT_SECONDS)

        try:
            s.connect((self.host, self.port))
            s.sendall(payload)
            s.shutdown(socket.SHUT_WR)

            reply = s.recv(1024)
            if not reply:
                print("[NET] No ACK received", flush=True)
                return False

            try:
                ack = json.loads(reply.decode("utf-8"))
            except Exception:
                print("[NET] Bad ACK payload:", reply, flush=True)
                return False

            ok = bool(ack.get("ok", False))
            ack_seq = ack.get("seq", None)

            if ok and ack_seq == self._seq:
                return True

            print("[NET] ACK mismatch:", ack, "expected seq", self._seq, flush=True)
            return False

        except Exception as e:
            print("[NET] send/ack error:", e, flush=True)
            return False

        finally:
            try:
                s.close()
            except Exception:
                pass

def setup_gpio(is_pi):
    if not is_pi:
        print("[MODE] PC mode -> sending commands to Pi over network", flush=True)
        print("[NET] PI_HOST =", PI_HOST, "PI_PORT =", PI_PORT, flush=True)
        return NetGPIO(PI_HOST, PI_PORT)
    print("[MODE] PI detected but this is PC controller code. Using DummyGPIO.", flush=True)
    return DummyGPIO()

# ----------------------------
# Voice recognition thread (PC)
# ----------------------------

def start_voice_thread(vosk_model_path, audio_device_index):
    audio_q = queue.Queue()
    state = {"last_command": None, "last_time": 0.0}

    def audio_callback(indata, frames, time_info, status):
        audio_q.put(bytes(indata))

    def voice_thread():
        try:
            if not os.path.isdir(vosk_model_path):
                print("[VOICE] Model folder not found:", vosk_model_path, flush=True)
                return

            if PRINT_AUDIO_DEVICES_ON_START:
                print("[VOICE] Audio input devices:", flush=True)
                devs = sd.query_devices()
                for i, d in enumerate(devs):
                    ins = d.get("max_input_channels", 0)
                    if ins and ins > 0:
                        print(" ", i, d.get("name", ""), "in=", ins, "sr=", d.get("default_samplerate", ""), flush=True)

            dev_info = sd.query_devices(audio_device_index, "input") if audio_device_index is not None else sd.query_devices(None, "input")
            device_rate = int(dev_info["default_samplerate"])
            blocksize = int(device_rate * 0.25)

            print("[VOICE] Using device:", dev_info.get("name", ""), flush=True)
            print("[VOICE] samplerate =", device_rate, "blocksize =", blocksize, flush=True)

            model = Model(vosk_model_path)
            rec = KaldiRecognizer(model, device_rate, json.dumps(PHRASES))
            rec.SetWords(True)

            with sd.RawInputStream(
                samplerate=device_rate,
                blocksize=blocksize,
                dtype="int16",
                channels=1,
                callback=audio_callback,
                device=audio_device_index
            ):
                print("[VOICE] Listening...", flush=True)
                while True:
                    data = audio_q.get()
                    if rec.AcceptWaveform(data):
                        result = json.loads(rec.Result())
                        text = (result.get("text") or "").strip().lower()
                        if not text:
                            continue
                        if text not in ARM_COMMANDS and text not in DISARM_COMMANDS:
                            continue

                        words = result.get("result", [])
                        if not words:
                            continue

                        avg_conf = sum(w.get("conf", 0.0) for w in words) / len(words)
                        if avg_conf < MIN_CONF:
                            continue

                        state["last_command"] = text
                        state["last_time"] = time.time()
                        print("[VOICE] OK text =", repr(text), "conf =", round(avg_conf, 2), flush=True)

        except Exception as e:
            print("[VOICE] Error:", e, flush=True)
            traceback.print_exc()

    threading.Thread(target=voice_thread, daemon=True).start()
    return state

# ----------------------------
# Main (PC controller)
# ----------------------------

def main():
    is_pi = (platform.system().lower() == "linux") and os.path.exists("/sys/class/gpio")

    vosk_model_path = VOSK_MODEL_PATH_PI if is_pi else VOSK_MODEL_PATH_WINDOWS
    audio_device_index = None if is_pi else AUDIO_DEVICE_INDEX_WINDOWS

    print("========================================", flush=True)
    print("[DEBUG] Python:", sys.version.replace("\n", " "), flush=True)
    print("[DEBUG] OS:", platform.platform(), flush=True)
    print("[DEBUG] is_pi:", is_pi, flush=True)
    print("[DEBUG] CWD:", os.getcwd(), flush=True)
    print("[DEBUG] Vosk path:", vosk_model_path, flush=True)
    print("[DEBUG] Audio device index:", audio_device_index, flush=True)
    print("[DEBUG] USE_MSMF_BACKEND:", USE_MSMF_BACKEND, flush=True)
    print("========================================", flush=True)

    gpio = setup_gpio(is_pi)
    voice_state = start_voice_thread(vosk_model_path, audio_device_index)

    cap = open_camera()

    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    if face_cascade.empty():
        raise RuntimeError("Failed to load Haar cascade.")

    armed = False
    arm_time = None
    trigger_on_time = None

    pending_arm_ack = False
    pending_fire_ack = False
    last_sent_servo_deg = None

    lock_start_time = None
    lock_reference_deg = None

    last_seen_face_time = None
    last_known_servo_deg = 90

    last_hb = time.time()
    hb_frames = 0
    hb_t0 = time.time()
    last_out = 0.0

    print("[MAIN] Running. Press q to quit.", flush=True)

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                continue

            hb_frames += 1

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(25, 25))

            face = max(faces, key=lambda r: r[2] * r[3]) if len(faces) > 0 else None

            if face is not None:
                x, y, fw, fh = face
                cv2.rectangle(frame, (x, y), (x + fw, y + fh), (0, 255, 0), 2)

                h, w = frame.shape[:2]
                face_center_x = x + (fw // 2)
                frame_center_x = w / 2.0

                error_x_px = face_center_x - frame_center_x
                error_x_deg = (error_x_px / max(1.0, w / 2.0)) * (CAMERA_HFOV_DEG / 2.0)

                servo_deg = SERVO_CENTER_DEG + SERVO_ALIGN_OFFSET_DEG + error_x_deg
                servo_deg = int(clamp(servo_deg, SERVO_MIN_DEG, SERVO_MAX_DEG))

                last_known_servo_deg = servo_deg
                last_seen_face_time = time.time()
            else:
                servo_deg = last_known_servo_deg

            cmd = voice_state.get("last_command", None)
            cmd_t = voice_state.get("last_time", 0.0)
            if cmd is not None and (time.time() - cmd_t) < 2.0:
                if cmd in ARM_COMMANDS and not armed and not pending_arm_ack:
                    pending_arm_ack = True
                    gpio.set_mosfet(True)
                    gpio.set_trigger(False)
                    ack_ok = gpio.send_and_wait_ack()

                    if ack_ok:
                        armed = True
                        arm_time = time.time()
                        trigger_on_time = None
                        lock_start_time = None
                        lock_reference_deg = None
                        last_sent_servo_deg = None
                        print("[STATE] ARMED = TRUE (Pi ACK)", flush=True)
                    else:
                        print("[STATE] ARM command not acknowledged by Pi", flush=True)

                    pending_arm_ack = False

                elif cmd in DISARM_COMMANDS and armed:
                    gpio.set_mosfet(False)
                    gpio.set_trigger(False)
                    gpio.send_and_wait_ack()

                    armed = False
                    arm_time = None
                    trigger_on_time = None
                    pending_fire_ack = False
                    lock_start_time = None
                    lock_reference_deg = None
                    last_sent_servo_deg = None
                    print("[STATE] ARMED = FALSE", flush=True)

                voice_state["last_command"] = None

            now = time.time()

            motor_on = False
            launch = False
            phase = "IDLE"

            if armed and arm_time is not None:
                elapsed = now - arm_time
                face_present = (
                    face is not None or
                    (last_seen_face_time is not None and (now - last_seen_face_time) <= FACE_HOLD_SECONDS)
                )
                can_fire = True
                if REQUIRE_FACE_TO_FIRE and not face_present:
                    can_fire = False

                if elapsed < MOTOR_SPIN_SECONDS:
                    motor_on = True
                    phase = "MOTOR"
                    lock_start_time = None
                    lock_reference_deg = None
                else:
                    motor_on = True
                    phase = "AIM"

                    if can_fire:
                        if lock_reference_deg is None:
                            lock_reference_deg = servo_deg
                            lock_start_time = now
                        else:
                            if abs(servo_deg - lock_reference_deg) <= LOCK_WINDOW_DEG:
                                if lock_start_time is None:
                                    lock_start_time = now
                            else:
                                lock_reference_deg = servo_deg
                                lock_start_time = now

                        locked = (
                            lock_start_time is not None and
                            (now - lock_start_time) >= LOCK_HOLD_SECONDS
                        )

                        if locked and trigger_on_time is None and not pending_fire_ack:
                            pending_fire_ack = True
                            gpio.set_mosfet(True)
                            gpio.set_trigger(True)
                            ack_ok = gpio.send_and_wait_ack()

                            if ack_ok:
                                trigger_on_time = now
                                print("[STATE] FIRE START (Pi ACK)", flush=True)
                            else:
                                print("[STATE] FIRE command not acknowledged by Pi", flush=True)

                            pending_fire_ack = False

                    else:
                        lock_start_time = None
                        lock_reference_deg = None

                    if trigger_on_time is not None:
                        phase = "FIRE"
                        launch = True
                        motor_on = True

                        if (now - trigger_on_time) >= TRIGGER_PULSE_SECONDS:
                            gpio.set_mosfet(False)
                            gpio.set_trigger(False)
                            gpio.send_and_wait_ack()

                            launch = False
                            trigger_on_time = None
                            armed = False
                            arm_time = None
                            lock_start_time = None
                            lock_reference_deg = None
                            last_sent_servo_deg = None
                            phase = "DISARMED"
                            print("[STATE] FIRED -> AUTO DISARM", flush=True)
            else:
                motor_on = False
                launch = False
                trigger_on_time = None
                arm_time = None
                lock_start_time = None
                lock_reference_deg = None

            if armed and trigger_on_time is None:
                if (last_sent_servo_deg is None) or (abs(servo_deg - last_sent_servo_deg) >= SERVO_STEP_DEADBAND_DEG):
                    gpio.set_servo_deg(servo_deg)
                    gpio.set_mosfet(motor_on)
                    gpio.set_trigger(False)

                    ack_ok = gpio.send_and_wait_ack()
                    if ack_ok:
                        last_sent_servo_deg = servo_deg
                    else:
                        print("[NET] Servo update not acknowledged", flush=True)

            if now - last_out >= WINDOWS_OUT_PRINT_EVERY:
                t = 0.0 if arm_time is None else max(0.0, now - arm_time)
                lock_age = 0.0 if lock_start_time is None else max(0.0, now - lock_start_time)
                print("[OUT] servo_deg =", servo_deg, "faces =", len(faces),
                      "armed =", armed, "motor_on =", motor_on, "launch =", launch,
                      "phase =", phase,
                      "t_since_arm =", round(t, 2),
                      "lock_age =", round(lock_age, 2),
                      flush=True)
                last_out = now

            status_text = "ARMED" if armed else "DISARMED"
            cv2.putText(frame, status_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
            cv2.putText(frame, "servo: " + str(int(servo_deg)), (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
            cv2.putText(frame, f"phase: {phase}", (10, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)

            if armed and lock_start_time is not None and trigger_on_time is None:
                lock_age = max(0.0, now - lock_start_time)
                cv2.putText(
                    frame,
                    f"lock: {lock_age:.2f}/{LOCK_HOLD_SECONDS:.2f}",
                    (10, 135),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.9,
                    (255, 255, 255),
                    2
                )

            if launch:
                cv2.putText(frame, "LAUNCH", (10, 170), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)

            cv2.imshow("PC_controller_sequence_one_shot", frame)
            if (cv2.waitKey(1) & 0xFF) == ord("q"):
                break

            if now - last_hb >= HEARTBEAT_SECONDS:
                dt = now - hb_t0
                fps = (hb_frames / dt) if dt > 0 else 0.0
                print("[HB] fps =", round(fps, 1), "faces =", len(faces),
                      "armed =", armed, "motor_on =", motor_on, "launch =", launch,
                      "phase =", phase,
                      flush=True)
                last_hb = now
                hb_t0 = now
                hb_frames = 0

    finally:
        try:
            cap.release()
        except Exception:
            pass
        cv2.destroyAllWindows()
        gpio.cleanup()
        print("[MAIN] Clean exit.", flush=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[TOPLEVEL] Fatal error:", e, flush=True)
        traceback.print_exc()
    finally:
        if platform.system().lower() == "windows":
            input("Press Enter to close...")