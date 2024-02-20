#!/usr/bin/env python3

# Radiometric Region of Interest (ROI) viewer for FLIR Lepton thermal cameras
# Developed & tested with a Lepton 3.5 on PureThermal3 USB-C board (www.groupgets.com)
# Using pyQt 5 and pyQTgraph as GUI toolkit.
#
# Features:
# - Radiometric data processing, display using different color maps
# - up to 10 rectangular ROIs (draggable by mouse, rotatable & scalable via small handles)  
#    - add new ROI via button, delete via right click on ROI or button (last one added)
# - min/max & average temperature display in table for full frame and each ROI
# - screenshot capture (via GUI button or TCP client, default port 8123)
# - reports temperature data to TCP client in JSON format
# - load/store ROI configurations and screenshot folder to YAML config file
#
# Further notes and implementation commented in ROIviewer.py.
# Reading of raw frame data via modified libuvc library (see uvctypes.py) follows 
# example of manufacturer: https://github.com/groupgets/purethermal1-uvc-capture/blob/master/python/
#
# Author: Oliver Keller (GSI/FAIR), o.keller [at] gsi.de, 2023-2024
# Released as open source under the permissive BSD 3-Clause License.
# See LICENSE file for details.


import time
import numpy as np
from queue import Queue
import sys
from PyQt5 import QtWidgets
import signal


from ROIviewer import FLIRROIWindow
from uvctypes import *


# number of frames stored in FIFO buffer, should be at least 2
# larger values increase latency but slighly improve display update rate
BUFFER_SIZE = 3        
q = Queue(BUFFER_SIZE)

signal.signal(signal.SIGINT, signal.SIG_DFL) # enable exit via Ctrl-C on terminal

def py_frame_callback(frame, userptr):
    array_pointer = cast(frame.contents.data, POINTER(c_uint16 * (frame.contents.width * frame.contents.height)))
    data = np.frombuffer(
        array_pointer.contents, dtype=np.dtype(np.uint16)
    ).reshape(
        frame.contents.height, frame.contents.width
    ) # no copy 
    # data = np.fromiter(
    #   frame.contents.data, dtype=np.dtype(np.uint8), count=frame.contents.data_bytes
    # ).reshape(
    #   frame.contents.height, frame.contents.width, 2
    # ) # copy

    if frame.contents.data_bytes != (2 * frame.contents.width * frame.contents.height):
        return
    if not q.full():
        q.put(data)
    else:
        #print("Buffer queue is full!")
        pass


if __name__ == "__main__":
    ctx = POINTER(uvc_context)()
    dev = POINTER(uvc_device)()
    devh = POINTER(uvc_device_handle)()
    ctrl = uvc_stream_ctrl()
    PTR_PY_FRAME_CALLBACK = CFUNCTYPE(None, POINTER(uvc_frame), c_void_p)(py_frame_callback)
    SIMULATE = False


    res = libuvc.uvc_init(byref(ctx), 0)
    if res < 0:
        print("uvc_init error")

    res = libuvc.uvc_find_device(ctx, byref(dev), PT_USB_VID, PT_USB_PID, 0)
    if res < 0:
        print("Could not find FLIR USB device")
        print("!!! FLIR frame simulation active !!!")
        SIMULATE = True
    
    if not SIMULATE:
        res = libuvc.uvc_open(dev, byref(devh))
        if not (res < 0):
            print("FLIR USB device opened")

            # print frame resultion etc.
            # print_device_info(devh)
            # print_device_formats(devh)

            frame_formats = uvc_get_frame_formats_by_guid(devh, VS_FMT_GUID_Y16)
            if len(frame_formats) == 0:
                print("device does not support Y16")
                exit(1)

            libuvc.uvc_get_stream_ctrl_format_size(devh, byref(ctrl), UVC_FRAME_FORMAT_Y16,
                frame_formats[0].wWidth, frame_formats[0].wHeight, int(1e7 / frame_formats[0].dwDefaultFrameInterval)
            )

            res = libuvc.uvc_start_streaming(devh, byref(ctrl), PTR_PY_FRAME_CALLBACK, None, 0)
            if res < 0:
                print("uvc_start_streaming failed: {0}".format(res))
                exit(1)
            try:
                frame = q.get(block=True, timeout=1)
            except:
                print("no camera feed")
                #exit(1)
        
    app = QtWidgets.QApplication(sys.argv)
    #time.sleep(2)
    window = FLIRROIWindow(q,simulate=SIMULATE)
    window.show()
    window.start_video()
    sys.exit(app.exec_())
