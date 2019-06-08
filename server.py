#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import json
import threading
import subprocess

from flask import Flask, request, abort, jsonify
from flask_restful import Api, Resource, url_for
import bluepy
from bluepy.btle import Scanner
from pykeigan import blecontroller

from device_info import devices

app = Flask(__name__)
api = Api(app)

# モータ接続dict初期化
dev_list = {}
for key in devices:
    dev_list[key] = None

dev_rad_opt = {}
for key in devices:
    dev_rad_opt[key] = None

# モータ−情報初期化
motor_info = {}

motion_pattern = {}

motion_patterns = {}
# モーションパターンファイル読込
motion_pattern_paths = os.path.dirname(os.path.abspath(__file__)) + "/motion_patterns.json" 
try:
    with open(motion_pattern_paths) as f:
        motion_patterns = json.load(f)
except Exception as e:
    print(e)
    motion_patterns = {}

# 周囲にあるモーターのデバイス情報を読み込む
class KMScan(Resource):
    def get(self):
        scanner = Scanner()
        devices = scanner.scan(5.0)
        device_info = []
        for dev in devices:
            for (adtype, desc, value) in dev.getScanData():
                if desc=="Complete Local Name" and "KM-1" in value:
                    info = {}
                    info["name"] = value
                    info["mac"] = dev.addr
                    device_info.append(info)
        return device_info

# モーターの位置を初期化する(現在地点の角度(rad)を0する)
class KMInit(Resource):
    def get(self, id):
        global dev_list
        if id not in dev_list:
            abort(404)
        try:
            dev = dev_list[id]
            dev.preset_position(0)
        except Exception as e:
            print(e)
            abort(500)
        return {"result" : "ok"}

# モーターを回転させる
class KMRotate(Resource):
    def get(self, id, rad):
        global dev_list
        global dev_rad_opt
        print(dev_list)
        if id not in dev_list:
            abort(404)
        dev = dev_list[id]
        device = devices[id]
        if dev == None:
            abort(403)
        try:
            rad_f = float(rad)
            if rad_f > device["max_rad"]:
                rad_f = device["max_rad"]
            elif rad_f < device["min_rad"]:
                rad_f = device["min_rad"]
            diff = motor_info[id]["position"] - dev_rad_opt[id]
            if (diff > 0.1 or diff < -0.1):
                abort(403)
            dev_rad_opt[id] = rad_f
            dev.move_to_pos(rad_f)
        except ValueError as e:
            print(e)
            abort(400)
        except bluepy.btle.BTLEDisconnectError as e:
            print(e)
            abort(500)
        except Exception as e:
            print(e)
            abort(500)
        return {"result" : "ok"}

class KMFetchMotorInfo(Resource):
    def get(self):
        global motor_info
        return motor_info

def connection(id):
    global dev_list
    if id not in dev_list:
        return
    if dev_list[id] != None:
        return
    device_info = devices[id]
    dev = blecontroller.BLEController(device_info["mac"])
    dev.enable_action()
    dev.enable_continual_imu_measurement()
    dev.stop_motor()
    dev.set_speed(device_info["speed"])
    return dev

def fetch_motor_info():
    global dev_list
    global dev_rad_opt
    global motor_info
    while True:
        for key, dev in dev_list.items():
            try:
                # 接続がなければ接続しに行く
                if dev == None:
                    dev = connection(key)
                    dev_list[key] = dev
                    time.sleep(2)
                    motor_info[key] = dev.read_motor_measurement()
                    motor_info[key].update(dev.read_imu_measurement())
                    dev_rad_opt[key] = motor_info[key]["position"]
                    print("{} has been connected".format(key))
                else:
                    motor_info[key] = dev.read_motor_measurement()
                    motor_info[key].update(dev.read_imu_measurement())
            except bluepy.btle.BTLEDisconnectError as e:
                dev_list[key] = None
            except Exception as e:
                print(e)
        # 2秒間隔でポーリング
        time.sleep(2)

# 周辺のモータースキャン
api.add_resource(KMScan, '/api/scan')

# モーター操作
## モーター位置初期化
## path: /api/init/<string:id>
## method: GET
## モーターの現在位置の角度を原点(rad = 0)とする
## id: モーターのID
##    motor1: ベース部分のモーター
##    motor2: 中間関節部分のモーター
##    motor3: 先端部分のモーター
##
## コード
##    200: 成功
##        return {"setatus" : "ok"}
##    404: 指定したIDを持つモーターが存在しない
##    500: その他サーバーエラー
api.add_resource(KMInit, '/api/init/<string:id>')
## モーター回転
## path: /api/rotate/<string:id>/rad/<string:rad>
## method: GET
## モーターを指定した絶対座標(rad)に移動する
## id: モーターのID
## rad: 移動させるモーターの角度
## motor1 - 3でそれぞれ角度の上限、下限がある
##     motor1:
##         min: -2.35
##         max: 2.35
##     motor2:
##         min: 0.00
##         max: 1.65
##     motor3:
##         min: 0.00
##         max: 6.28
##
## コード
##    200: 成功
##        return {"setatus" : "ok"}
##    403: 指定したIDを持つモーターとの接続が完了していない／モーターが操作できない状態にある
##    404: 指定したIDを持つモーターが存在しない
##    500: その他サーバーエラー
api.add_resource(KMRotate, '/api/rotate/<string:id>/rad/<string:rad>')
## モーター情報取得
## path: /api/fetch
## method: GET
## 下記フォーマットでモーターの情報を取得する
##  {
##    "motor1": {                                   // モーターID
##      "position": -1.4906458854675293,            // モーターの角度(rad)
##      "velocity": 0.0036211530677974224,          // ベロシティ（よくわかっていないです）
##      "torque": 0.013509166426956654,             // トルクの強さ
##      "received_unix_time": 1559976978.0912964,   // 取得時間(UNIX Time)
##      "accel_x": 0.0017090365306558428,           // 加速度x
##      "accel_y": -0.012695699942014832,           // 加速度y
##      "accel_z": 0.9739066743980224,              // 加速度z
##      "temp": 36.416179950280046,                 // 多分温度
##      "gyro_x": 0.02210491026,                    // モーター傾きx（≠回転量）
##      "gyro_y": 0.02463499035,                    // モーター傾きy（≠回転量）
##      "gyro_z": 0.00253008009                     // モーター傾きz（≠回転量）
##    }
##  }
## 情報が取得できないモーターは未接続
api.add_resource(KMFetchMotorInfo, '/api/fetch')

class KMMotionPatternInit(Resource):
    def get(self):
        global motion_pattern
        motion_pattern = {}
        return {"result" : "ok"}

class KMMotionPatternSave(Resource):
    def get(self, pattern_name):
        global motion_pattern
        global motion_patterns
        global motion_pattern_paths
        index = len(motion_patterns)
        motion_patterns[index] = motion_pattern
        subprocess.run("echo {} >> {}".format(json.dumps(motion_patterns), motion_pattern_paths), shell=True)
        return {"result" : "ok", "pattern_id" : index}

class KMMotionPatternPlay(Resource):
    def get(self):
        #TODO 再生処理
        return {"result" : "ok"}

class KMMotionAdd(Resource):
    def get(self, id, index, rad, speed):
        global motion_pattern
        if id not in dev_list:
            abort(404)
        try:
            index = int(index)
            rad = float(rad)
            speed = float(speed)
            motion_pattern[index] = {
                "id" : id,
                "rad" : rad,
                "speed" : speed
            }
        except ValueError as e:
            print(e)
            abort(400)
        except Exception as e:
            print(e)
            abort(400)
        return {"result" : "ok"}

class KMMotionDelelte(Resource):
    def get(self, index):
        global motion_pattern
        if index not in motion_pattern:
            abort(404)
        motion_pattern.pop(index)
        return {"result" : "ok"}

class KMMotionPatterns(Resource):
    def get(self):
        global motion_patterns
        return motion_patterns

class KMMotionPattern(Resource):
    def get(self, pattern_id):
        global motion_patterns
        if pattern_id not in motion_patterns:
            abort(404)
        # TODO パターン再生
        return 

    def delete(self, pattern_id):
        global motion_patterns
        if pattern_id not in motion_patterns:
            abort(404)
        motion_patterns.pop(pattern_id)
        subprocess.run("echo {} >> {}".format(json.dumps(motion_patterns), motion_pattern_paths), shell=True)
        return

# モーションパターン
## モーションパターン登録開始/削除
api.add_resource(KMMotionPatternInit, '/api/motion_pattern/init')
## モーションパターン登録完了
api.add_resource(KMMotionPatternSave, '/api/motion_pattern/save/<string:pattern_name>')
## 登録中のモーションパターン再生
api.add_resource(KMMotionPatternPlay, '/api/motion_pattern/play')
## モーション登録
api.add_resource(KMMotionAdd, '/api/motion/<string:id>/index/<string:index>/rad/<string:rad>/speed/<string:speed>')
## モーション登録削除
api.add_resource(KMMotionDelelte, '/api/motion/<string:id>')
## モーションパターン一覧表示
api.add_resource(KMMotionPatterns, '/api/motion_patterns')
## モーションパターン再生／削除
api.add_resource(KMMotionPattern, '/api/motion_patterns/<string:pattern_id>')

if __name__ == "__main__":
    # モータ情報取得スレッド起動
    th =threading.Thread(target=fetch_motor_info)
    th.start()
    # サーバ起動
    app.run(host="0.0.0.0", port=10080)
