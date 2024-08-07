from flask import Flask, request, jsonify
from flask_cors import CORS
import asyncio
import websockets
import logging
import requests
import json
import datetime
import pytz
import numpy as np
import supervision as sv
from ultralytics import YOLO
import os
import glob
import base64
import cv2

app = Flask(__name__)
CORS(app) 

UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
    
logging.basicConfig(level=logging.INFO)

clients = set()

log_id = None
box_id = None
item_type = None
user_id = None
start_time = None
image_data = None
frame = None
logs = []

IST = pytz.timezone('Asia/Kolkata')

model = YOLO(os.path.relpath("best.pt"))
model.fuse()

LINE_START = sv.Point(150, 1000)
LINE_END = sv.Point(1100, 100)

line_counter = sv.LineZone(start=LINE_START, end=LINE_END)

line_annotator = sv.LineZoneAnnotator(
    thickness=4, 
    text_thickness=4, 
    text_scale=2
)

box_annotator = sv.BoxAnnotator(
    thickness=4,
    text_thickness=4,
    text_scale=2
)

in_count = 0
out_count = 0

video_writer = None

def initialize_video_writer(frame):
    global video_writer, log_id
    height, width, _ = frame.shape
    video_writer = cv2.VideoWriter(f"{UPLOAD_FOLDER}\{log_id}.mp4", cv2.VideoWriter_fourcc(*'mp4v'), 30, (width, height))

def save_frame_to_video(frame):
    global video_writer
    if video_writer is not None:
        video_writer.write(frame)

def model_run(frame):
    global in_count,out_count,video_writer

    frame = cv2.resize(frame, (1920, 1080))
    
    if video_writer is None:        
        initialize_video_writer(frame)

    results = model.track(source=frame, tracker='bytetrack.yaml', show=False, agnostic_nms=True, persist=True)

    for result in results:
        detections = sv.Detections.from_yolov8(result)
        if result.boxes.id is not None:
            detections.tracker_id = result.boxes.id.cpu().numpy().astype(int)
        labels = [
            f"{tracker_id} {model.model.names[class_id]} {confidence:0.2f}"
            for _, confidence, class_id, tracker_id
            in detections
        ]
        line_counter.trigger(detections=detections)
        line_annotator.annotate(frame=frame, line_counter=line_counter)
        frame = box_annotator.annotate(
            scene=frame, 
            detections=detections,
            labels=labels
        )
        save_frame_to_video(frame)

        in_count = line_counter.in_count
        out_count = line_counter.out_count
        
        time = datetime.datetime.now().isoformat()
        time_dt = datetime.datetime.fromisoformat(time)
        time_str = time_dt.strftime("%Y-%m-%d %H:%M:%S")

        log = f"{time_str}    Current-Count: {in_count}  ->  {labels}"
        logs.append(log + "\n")

async def handler(websocket, path):
    global log_id, box_id, item_type, user_id, start_time, logs,frame, video_writer
    clients.add(websocket)
    try:
        initial_data = await websocket.recv()
        get_data(initial_data)
        while True:
            try:
                data = await websocket.recv()
                model_run(frame)
            except:
                logging.info("Client disconnected")
                break
    finally:
        clients.remove(websocket)
        send_data_to_backend()
        await websocket.close()

        if video_writer is not None:
            video_writer.release()
            video_writer = None

def send_data_to_backend():
    global log_id, box_id, item_type, user_id, start_time, logs
    logs_str = " ".join(logs)

    end_time = datetime.datetime.now().isoformat()

    payload = {
        "logId": log_id,
        "boxId": box_id,
        "itemType": item_type,
        "userId": user_id,
        "totalCount": 0,
        "startTime": start_time,
        "endTime": end_time,
        "fullLogFile": logs_str
    }

    try:
        response = requests.post("http://localhost:8007/log/api/Log", json=payload)
        response.raise_for_status()
        logging.info(f"Successfully sent data to endpoint: {response.status_code}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error sending data to endpoint: {e}")

def get_data(data):
    global log_id, box_id, item_type, user_id, start_time,frame
    data_lines = data.decode("utf-8").split("\n")
    for line in data_lines:
        if line.startswith("LogId:"):
            log_id = line.split(":")[1]
        elif line.startswith("BoxId:"):
            box_id = line.split(":")[1]
        elif line.startswith("ItemType:"):
            item_type = line.split(":")[1]
        elif line.startswith("UserId:"):
            user_id = line.split(":")[1]
        elif line.startswith("ImageData:"):
            image_data = line.split(":")[1]
            image_bytes = base64.b64decode(image_data)
            np_arr = np.frombuffer(image_bytes, np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    start_time = datetime.datetime.now().isoformat()
    
if __name__ == "__main__":
    start_server = websockets.serve(handler, host='0.0.0.0', port=8009)
    asyncio.get_event_loop().run_until_complete(start_server)
    asyncio.get_event_loop().run_forever()
