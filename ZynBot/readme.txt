ZYNBOT README
=============

Overview
--------
This README documents the current PC-side Python script and Raspberry Pi receiver script you uploaded. It is focused on setup, environment, dependencies, device selection, networking, and debugging for the camera, audio, face-tracking, TCP communication, and GPIO portions of the project.

This README does not document target-and-launch operation. Use it for setup, calibration, testing, and debugging only.

Files
-----
PC controller script:
- final.py

Pi receiver script:
- pi_receiver.py

What each side does
-------------------
PC side
- Opens a camera feed
- Finds a face using OpenCV Haar cascade
- Runs voice recognition with Vosk
- Sends TCP JSON messages to the Raspberry Pi with:
  - seq
  - servo_deg
  - armed
  - launch

Pi side
- Listens for TCP connections on port 5005
- Receives the JSON message from the PC
- Converts servo angle to pulse width with pigpio
- Controls:
  - aim servo on GPIO 12
  - pusher servo on GPIO 13
  - MOSFET on GPIO 26

Current important values from your code
---------------------------------------
PC script
- DROIDCAM_URLS:
  - http://(your address)/video
  - http://(your address)/mjpegfeed
- VOSK_MODEL_PATH_WINDOWS:
  - C:\...\ZynBot\Webcam_Python\vosk-model-small-en-us-0.15
- AUDIO_DEVICE_INDEX_WINDOWS = Find using the scipt. Look for Droidcam virtual audio
- PI_HOST
- PI_PORT = 5005
- CAMERA_TRY_URL_FIRST = False //this sets it for usb connection
- PREFERRED_CAMERA_KEYWORDS = ["droidcam"]

Pi script
- AIM_SERVO_GPIO = 12
- PUSHER_SERVO_GPIO = 13
- MOSFET_GPIO = 26
- HOST = 0.0.0.0
- PORT = 5005
- AIM_SERVO_MIN_US = 2200
- AIM_SERVO_MAX_US = 1550
- PUSHER_HOME_US = 2200
- PUSHER_FIRE_US = 1500

Folder layout
-------------
Suggested Windows side layout:
C:\Users\icicl\Desktop\ZynBot\
    Webcam_Python\
        final.py
        vosk-model-small-en-us-0.15\
        venv\

Suggested Raspberry Pi layout:
~/zynbot/
    pi_receiver.py
    venv/

Windows setup
-------------
Open Command Prompt and go to the project folder:

cd C:\Users\icicl\Desktop\ZynBot\Webcam_Python

Create a virtual environment:

python -m venv venv

Activate it:

venv\Scripts\activate

Install packages:

pip install opencv-python sounddevice vosk pygrabber
pip install soundfile

About the virtual environment
-----------------------------
The venv folder is a local Python environment for this project. It keeps your project packages separate from the rest of Windows so package versions do not clash with other Python work.

Useful venv notes:
- Create it once with:
  python -m venv venv
- Activate each time before running the script:
  venv\Scripts\activate
- If activation worked, your prompt usually starts with:
  (venv)
- To leave the venv:
  deactivate

If pip is broken or missing, try:
python -m pip install --upgrade pip

If you ever need to rebuild the environment:
1. delete the venv folder
2. run python -m venv venv
3. activate it again
4. reinstall the packages

Pi startup
----------
On the Raspberry Pi:

sudo systemctl start pigpiod
cd ~/zynbot
source venv/bin/activate
python3 pi_receiver.py

About pigpio
------------
The Pi receiver uses pigpio, not plain RPi.GPIO. That means pigpiod must be running before the script starts.

GPIO pins
---------
Current pin assignments from your setup:
- GPIO SERVO_GPIO = 12
- GPIO TRIGGER_GPIO = 13
- GPIO MOSFET_GPIO = 26

In the Pi file those are named:
- AIM_SERVO_GPIO = 12
- PUSHER_SERVO_GPIO = 13
- MOSFET_GPIO = 26

DroidCam setup
--------------
Your PC code supports two different camera approaches:

1. URL stream mode
   The script can connect directly to the phone over HTTP using:
   - /video
   - /mjpegfeed

2. Windows camera device mode
   The script can scan Windows camera devices and prefer a device whose name contains "droidcam"

Your code currently has:
- CAMERA_TRY_URL_FIRST = False
- PREFERRED_CAMERA_KEYWORDS = ["droidcam"]

So right now it will usually prefer scanning Windows camera devices first instead of immediately trying the raw URL.

If DroidCam is not being found correctly, check:
- the DroidCam app is open on the phone
- the Windows DroidCam client is running if you are using Windows-device mode
- video is enabled in DroidCam
- no other app is already using the DroidCam feed
- the phone and PC are on the same network if you are using URL mode
- the IP address in DROIDCAM_URLS matches the phone's current IP

Changing the DroidCam video connection
--------------------------------------
If your phone IP changes, update:

DROIDCAM_URLS = [
    "http://YOUR_PHONE_IP:4747/video",
    "http://YOUR_PHONE_IP:4747/mjpegfeed",
]

If you want the script to try the HTTP stream first, change:

CAMERA_TRY_URL_FIRST = True

If you want Windows device scan first, leave it False.

Audio device selection
----------------------
Your code uses:

AUDIO_DEVICE_INDEX_WINDOWS = 14

That number can change from one PC to another. It depends on which audio devices Windows currently has installed and the order that sounddevice sees them.

The PC script already prints available input devices when it starts if:
PRINT_AUDIO_DEVICES_ON_START = True

That is useful because you can run the script, look at the printed list, find the line for something like:
Microphone (DroidCam Virtual Audio)

and then change AUDIO_DEVICE_INDEX_WINDOWS to match that device number on your machine.

If voice recognition is not hearing anything:
- verify the DroidCam audio driver is installed
- check Windows Sound settings
- make sure the correct mic index is selected
- confirm the device has input channels in the printed list
- make sure the phone microphone is enabled in DroidCam

Vosk model path
---------------
Your code expects the Windows model here:

C:\Users\icicl\Desktop\ZynBot\Webcam_Python\vosk-model-small-en-us-0.15

If your project is stored somewhere else, or if you rename the folder, you must update:

VOSK_MODEL_PATH_WINDOWS = r"YOUR_NEW_PATH"

The script checks whether the folder exists and prints an error if it does not.

Pi network connection
---------------------
The PC script sends messages to:

PI_HOST = 192.168.4.33
PI_PORT = 5005

The Pi script listens on:
HOST = "0.0.0.0"
PORT = 5005

That means the Pi will accept connections on port 5005 from any network interface, but the PC must still know the correct Pi IP address. If the Pi address changes, update PI_HOST in the PC script.

How to run the current code for setup and debugging
---------------------------------------------------
1. Start the Pi side first
   sudo systemctl start pigpiod
   cd ~/zynbot
   source venv/bin/activate
   python3 pi_receiver.py

2. On the Windows PC:
   cd C:\Users\icicl\Desktop\ZynBot\Webcam_Python
   venv\Scripts\activate
   python final.py

3. Watch the debug output on both sides

The PC code already prints useful startup info, including:
- Python version
- OS
- current working directory
- Vosk model path
- audio device index
- camera debug info
- heartbeat lines
- network ACK failures

What the JSON messages look like
--------------------------------
The PC sends JSON shaped like this:

{
  "seq": 1,
  "servo_deg": 90,
  "armed": false,
  "launch": false
}

The Pi replies with:

{
  "ok": true,
  "seq": 1
}

Servo calibration notes
-----------------------
The Pi script converts degrees to pulse widths using:

deg_to_us(deg, AIM_SERVO_MIN_US, AIM_SERVO_MAX_US)

Your current aim-servo calibration is:
- AIM_SERVO_MIN_US = 2200
- AIM_SERVO_MAX_US = 1550

That is a reversed direction mapping compared with many normal setups, but it is valid if it matches how your servo is mounted. If the servo moves opposite of what you expect, this is one of the first things to check.

Useful PC-side tuning values
----------------------------
These settings may need adjustment depending on your camera position, field of view, and alignment:

CAMERA_HFOV_DEG
- Used to turn face position in the frame into a servo angle

SERVO_CENTER_DEG
- The logical center position

SERVO_ALIGN_OFFSET_DEG
- Small left-right correction if the camera and turret are not perfectly aligned

SERVO_MIN_DEG / SERVO_MAX_DEG
- Limits how far the servo can rotate

SERVO_STEP_DEADBAND_DEG
- Prevents tiny servo updates from being sent constantly

CAMERA_SCAN_MAX_INDEX
- How many Windows device indices to try

PREFERRED_CAMERA_KEYWORDS
- Camera names to prioritize when scanning devices

Troubleshooting
---------------
1. "Model folder not found"
- Fix VOSK_MODEL_PATH_WINDOWS

2. Camera opens but no frames
- close any app that may already be using DroidCam
- try CAMERA_TRY_URL_FIRST = True
- verify the phone IP in DROIDCAM_URLS
- verify the Windows DroidCam client is running if using device mode

3. Wrong microphone
- read the printed device list
- change AUDIO_DEVICE_INDEX_WINDOWS

4. Pi does not receive anything
- verify PI_HOST matches the Pi IP
- verify both devices are on the same network
- verify pi_receiver.py is running
- verify port 5005 is not blocked

5. pigpio error on the Pi
- run:
  sudo systemctl start pigpiod

6. Servo moves backward
- swap or retune AIM_SERVO_MIN_US and AIM_SERVO_MAX_US

7. Network ACK errors on PC
- Pi script may not be running
- wrong PI_HOST
- wrong port
- firewall or network problem

Quick command summary
---------------------
Windows:
cd C:\Users\icicl\Desktop\ZynBot\Webcam_Python
python -m venv venv
venv\Scripts\activate

Install Packages:
pip install opencv-python sounddevice vosk pygrabber
pip install soundfile

Pi startup:
sudo systemctl start pigpiod
cd ~/zynbot
source venv/bin/activate
python3 pi_receiver.py
