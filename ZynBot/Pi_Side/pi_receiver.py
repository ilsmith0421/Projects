import socket
import json
import time
import pigpio

# GPIO pins
AIM_SERVO_GPIO = 12
PUSHER_SERVO_GPIO = 13
MOSFET_GPIO = 26

# Aim servo calibrated pulse range
AIM_SERVO_MIN_US = 2200   # left side of camera
AIM_SERVO_MAX_US = 1550   # right side of camera

# Pusher servo calibrated pulse values
PUSHER_HOME_US = 2200
PUSHER_FIRE_US = 1500

HOST = "0.0.0.0"
PORT = 5005
SERVO_DEADBAND_DEG = 1.0

def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v

def deg_to_us(deg, min_us, max_us):
    deg = clamp(float(deg), 0.0, 180.0)
    return int(min_us + (deg / 180.0) * (max_us - min_us))

def main():
    pi = pigpio.pi()
    if not pi.connected:
        raise RuntimeError("pigpio not connected. Start it with: sudo systemctl start pigpiod")

    pi.set_mode(MOSFET_GPIO, pigpio.OUTPUT)
    pi.write(MOSFET_GPIO, 0)

    # Start aim servo centered
    pi.set_servo_pulsewidth(
        AIM_SERVO_GPIO,
        deg_to_us(90, AIM_SERVO_MIN_US, AIM_SERVO_MAX_US)
    )

    # Start pusher servo at home position
    pi.set_servo_pulsewidth(PUSHER_SERVO_GPIO, PUSHER_HOME_US)

    last_servo_deg = 90.0

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(5)
    print(f"[PI] Listening on {HOST}:{PORT}")

    last_print = time.time()

    try:
        while True:
            conn, addr = srv.accept()
            with conn:
                data = b""
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk

                try:
                    msg = json.loads(data.decode("utf-8"))
                except Exception:
                    print("[PI] Bad message:", data[:100])
                    try:
                        conn.sendall(json.dumps({
                            "ok": False,
                            "seq": None,
                            "error": "bad_json"
                        }).encode("utf-8"))
                    except Exception:
                        pass
                    continue

                seq = msg.get("seq", None)
                servo_deg = float(msg.get("servo_deg", 90))
                armed = bool(msg.get("armed", False))
                launch = bool(msg.get("launch", False))

                # Aim servo
                if abs(servo_deg - last_servo_deg) >= SERVO_DEADBAND_DEG:
                    pi.set_servo_pulsewidth(
                        AIM_SERVO_GPIO,
                        deg_to_us(servo_deg, AIM_SERVO_MIN_US, AIM_SERVO_MAX_US)
                    )
                    last_servo_deg = servo_deg

                # Flywheel motor
                pi.write(MOSFET_GPIO, 1 if armed else 0)

                # Pusher servo
                if launch:
                    pusher_us = PUSHER_FIRE_US
                else:
                    pusher_us = PUSHER_HOME_US

                pi.set_servo_pulsewidth(PUSHER_SERVO_GPIO, pusher_us)

                # ACK back to PC
                try:
                    conn.sendall(json.dumps({
                        "ok": True,
                        "seq": seq
                    }).encode("utf-8"))
                except Exception as e:
                    print("[PI] Failed to send ACK:", e)

                now = time.time()
                if now - last_print >= 0.25:
                    print(
                        "[PI] seq =", seq,
                        "servo_deg =", int(servo_deg),
                        "servo_us =", deg_to_us(servo_deg, AIM_SERVO_MIN_US, AIM_SERVO_MAX_US),
                        "armed =", armed,
                        "launch =", launch,
                        "pusher_us =", pusher_us
                    )
                    last_print = now

    finally:
        pi.write(MOSFET_GPIO, 0)
        pi.set_servo_pulsewidth(AIM_SERVO_GPIO, 0)
        pi.set_servo_pulsewidth(PUSHER_SERVO_GPIO, 0)
        pi.stop()
        srv.close()
        print("[PI] Clean exit")

if __name__ == "__main__":
    main()
