# Radiometric region of interest (ROI) viewer class for FLIR Lepton (3.5) thermal camera
# 
# FLIRROIWindow class takes python Queue as frame in put. If 'simulate' init parameter is set to True, 
# it will create simulated readiometric FLIR frame data for testing without a FLIR camera 
#
# pyQt window displays pyqtgraph ROIs and temperature data in a table.
# ROI config states can be stored in and loaded from a config.yaml file.
# includes ColormapSelector sub-class for selecting color maps as combobox
# 
# Notes:
# - the ROI hover event is used to highlight the row corresponding of one ROI in the table with an asterisk '*', 
#   but there is no signal emitted when a ROI is un-hovered. Thus, the last hovered ROI will stay highlighted in the table.
#
# - the ROI type could be changed by selecting another pyqtgraph ROI class in the add_roi() method
#   Saving/loading of ROI states in YAML file may need to be adapted accordingly.
#   ROI docs: https://pyqtgraph.readthedocs.io/en/latest/api_reference/graphicsItems/roi.html
#   ROI type examples: https://github.com/pyqtgraph/pyqtgraph/blob/master/pyqtgraph/examples/ROItypes.py
#
# - more color maps can be added to the selectable_colormaps dictionary below

# - file name postfix and image format of screenshots can be adapted in __init___(), see initial lines of variable declarations
# 
# Author: Oliver Keller (GSI/FAIR), o.keller [at] gsi.de, 2023-2024
# Released as open source under the permissive BSD 3-Clause License.


from PyQt5 import QtWidgets, QtGui, QtCore
from PyQt5.QtWidgets import QPushButton, QVBoxLayout, QHBoxLayout, QWidget, QGridLayout
from PyQt5.QtNetwork import QTcpServer, QHostAddress
import pyqtgraph.exporters
import pyqtgraph as pg

import cv2
import numpy as np
import datetime
import yaml
import json

class FLIRROIWindow(QtWidgets.QMainWindow):
    def __init__(self, frame_queue, simulate=True, TCP_port=8123):
        super().__init__()

        # variables for screenshot file name
        
        self.folder = "screenshots" #default folder for screenshots, overwritten by YAML config
        self.prefix = "FLIR"        #default for manual screenshots, can be changed in GUI's text field
        self.postfix = "_%Y-%m-%d_%H-%M-%S_heatmap" #format string for embedding timestamp in file name
        self.image_format = ".tiff" # select image format for screenshots
        self.file_name = None       # later constructed as: self.folder + / + self.prefix + self.postfix + self.image_format

        # create a simulated frame data for testing without FLIR camera 
        # 4 fixed temperature rectangle plus +/- 5°C of random noise is added in update_frame() below
        # simulated frame size corresponds to FLIR Lepton 3.5 (data format simulates the 16bit radiometric mode)
        self.sim_frame_height   = 120 
        self.sim_frame_width    = 160 # 160x120 pixels of simulated frame data
        self.sim_frame = np.zeros((self.sim_frame_height, self.sim_frame_width), dtype=np.ushort)
        self.sim_frame[ 0:40,:] = self.ctok(40)     # 40°C rectangle, horizontal top right
        self.sim_frame[40:80,:] = self.ctok(30)     # 30°C rectangle, horizontal middle right
        self.sim_frame[80:120,:] = self.ctok(20)    # 20°C rectangle, horizontal bottom righ
        self.sim_frame[:,0:40] = self.ctok(50)      # 50°C rectangle, vertical left

        
        # colormaps must be already defined in both cv2 and matplotlib, 
        # defined color maps: https://docs.opencv.org/3.4/d3/d50/group__imgproc__colormap.html#ga9a805d8262bcbe273f16be9ea2055a65
        # for converting any matplotlib color map, this conversion could be used:
        # https://github.com/AlexanderProd/thermal-viewer/blob/66279c72a1b24cdd1ee3eb4546957a1a03096a23/src/utils.py#L8
        self.selectable_colormaps = {"jet"      : cv2.COLORMAP_JET, 
                                     "inferno"  : cv2.COLORMAP_INFERNO, 
                                     "gray"     : None,
                                     "hot"      : cv2.COLORMAP_HOT,
                                     "cool"     : cv2.COLORMAP_COOL, 
                                     "turbo"    : cv2.COLORMAP_TURBO}
        self.colormap = "jet"   # default colormap
        self.cm_selector_index =list(self.selectable_colormaps).index(self.colormap)

        self.scale = 8          # scale factor enlarging FLIR image in viewbox

        # BELOW HERE only internal variables
        
        # store init pramaeters
        self.frame_queue = frame_queue
        self.simulate = simulate

        # Create a QTimer to update the video feed
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.update_frame)

        self.snapshot = False
        self.first_frame = True

        self.temperatures= {}           # holds min, max & average temperatures of each ROI

        self.max_ROIs = 10              # each active ROI makes display and table update slower...
        self.ROIs=[]                    # will be populated by load_config() and add_roi()
        self.highlighted_roi = None     # index of ROI to be highlighted in table
        self.column_labels =["  frame"] + self.max_ROIs * [""] # stores column labels for table widget

        self.init_ui()
        self.load_config()
        
        self.tcpServer = QTcpServer(self)
        address = QHostAddress('0.0.0.0')
        if not self.tcpServer.listen(address, TCP_port):
            print("cannot open TCP port! exiting")
            self.close()
            return
        self.clientConnection = None
        self.tcpServer.newConnection.connect(self.client_connected)
        

    def init_ui(self):
        #self.showFullScreen()
        self.setGeometry(0, 0, 1850, 980)
        self.setWindowTitle("FLIR Lepton radiometric ROI viewer")

        # Create a horizontal layout for the toolbar and label
        horizontal_layout = QHBoxLayout()
        horizontal_layout.setContentsMargins(0, 0, 0, 0)

        # Create a toolbar widget and add buttons to it
        toolbar_widget = QWidget()
        toolbar_widget.setFixedWidth(400)
        toolbar_layout = QVBoxLayout(toolbar_widget)
        toolbar_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)

        roi_button_layout = QGridLayout()
        roi_button_layout.setContentsMargins(0, 0, 0, 0)

        add_roi_button = QPushButton("Add ROI")
        add_roi_button.clicked.connect(self.add_roi)
        del_roi_button = QPushButton("Del. ROI")
        del_roi_button.clicked.connect(self.del_roi)
        load_button = QPushButton("Load ROIs")
        load_button.clicked.connect(self.load_config)
        save_button = QPushButton("Save ROIs")
        save_button.clicked.connect(self.save_config)
        
        roi_button_layout.addWidget(add_roi_button,0,0)
        roi_button_layout.addWidget(del_roi_button,0,1)
        roi_button_layout.addWidget(load_button,1,0)
        roi_button_layout.addWidget(save_button,1,1)
        roi_button_layout.setColumnStretch(2,0)
    
        screenshot_button = QPushButton("Take Screenshot")
        screenshot_button.clicked.connect(self.take_screenshot)

        prefix_cm_layout = QGridLayout()
        prefix_cm_layout.setContentsMargins(0, 0, 0, 0)

        prefix_text = QtWidgets.QLabel("File prefix: ")
        self.prefix_input = QtWidgets.QLineEdit(self.prefix)
        self.prefix_input.setFixedWidth(185) # how to get actual width of add/del ROI buttons?
        cm_text = QtWidgets.QLabel("Color map: ")
        cm_selector = self.ColormapSelector(self,default_index=self.cm_selector_index)
        prefix_cm_layout.addWidget(prefix_text,0,0)
        prefix_cm_layout.addWidget(self.prefix_input,0,1)
        prefix_cm_layout.addWidget(cm_text,1,0)
        prefix_cm_layout.addWidget(cm_selector,1,1)
        prefix_cm_layout.setColumnStretch(2,0)

        quit_button = QPushButton("Quit")
        quit_button.clicked.connect(QtCore.QCoreApplication.quit)

        self.table = pg.TableWidget(sortable=False,editable=False)
        self.table.setFormat("%5.1f")
        self.table.setRowCount(len(self.column_labels))
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels([" min. "," avg. "," max. "])
        self.table.horizontalHeadersSet = True
        self.table.setVerticalHeaderLabels(self.column_labels)
        self.table.verticalHeadersSet = True

        toolbar_widget.setFont(QtGui.QFont('monospace', 14))

        # construct toolbar in the right
        toolbar_layout.addWidget(self.table)
        toolbar_layout.addLayout(roi_button_layout)
        toolbar_layout.addWidget(screenshot_button)
        toolbar_layout.addLayout(prefix_cm_layout)
        #toolbar_layout.addLayout(cm_layout)
        toolbar_layout.addWidget(quit_button)

        # the gl widget holds the image and color bar (isnide a viewbox)
        gl = pg.GraphicsLayoutWidget()
        self.image = pg.ImageItem()

        self.vb = gl.addViewBox(enableMouse=False,invertY=True)
        self.vb.setLimits(xMin=0, xMax=self.scale*self.sim_frame.shape[1],
                     minXRange=0, maxXRange=self.scale*self.sim_frame.shape[1],
                     yMin=0, yMax=self.scale*self.sim_frame.shape[0],
                     minYRange=0, maxYRange=self.scale*self.sim_frame.shape[0])
        self.vb.setAspectLocked(True)
        self.vb.addItem(self.image)

        self.cb = pg.ColorBarItem(interactive=False, colorMap=pg.colormap.getFromMatplotlib(self.colormap) ,\
                                    orientation='right', label='Temperature range of full frame in °C', width=20)
        #self.cb.setImageItem(self.image) # direct link between img and color bar does not work, need to apply colormap after image data is processed!

        gl.addItem(self.cb)

        self.roi_pen = pg.mkPen(pg.mkColor("red"),width=3)
        self.roi_hoverPen = pg.mkPen(pg.mkColor("orange"),width=3)
                
        # Add the toolbar and gl widget to the horizontal layout
        horizontal_layout.addWidget(gl)
        horizontal_layout.addWidget(toolbar_widget)
        
        # Create one (central) widget and set the horizontal layout as its main layout
        central_widget = QWidget()
        central_widget.setLayout(horizontal_layout)

        self.setCentralWidget(central_widget)

        #add timer that updates the table widget with temperature data
        self.table_timer = pg.QtCore.QTimer()
        self.table_timer.timeout.connect(self.update_table)

    def start_video(self):
        # Start the QTimer to update the video feed at a desired interval
        self.timer.start(int(1000/8))
        self.table_timer.start(250)


    def stop_video(self):
        # Stop the QTimer
        self.timer.stop()

    def update_frame(self):
        if self.simulate:
            # add +/- 5°C of random noise to the simulated frame
            raw_frame = self.sim_frame + np.random.randint(-500,500,size=(self.sim_frame_height,self.sim_frame_width)) 
        else:
            try:
                raw_frame = self.frame_queue.get(block=True, timeout=1)
            except:
                print("frame(s) skpipped")
                return
        
        if self.first_frame == True:
            # useful for debugging
            # print frame parameters
            # print(np.info(raw_frame))
            self.first_frame = False
        
        # apply self.scale to raw frame, output: scaled_frame
        # normalize and shift 16bit raw data to 8bit data copy, output: img (format uint8)
        scaled_frame, img = self.get_image(raw_frame,scale=self.scale)
        
        #img = cv2.applyColorMap(img, cv2.COLORMAP_JET)
        if self.selectable_colormaps[self.colormap] is not None: # gray => do not apply any map
            img = cv2.applyColorMap(img.astype(np.uint8), self.selectable_colormaps[self.colormap]) # result is in BGR!
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)  # convert from cv2's interal BGR to RGB fromat for display

        # finally render the colormapped image on screen
        self.image.setImage(img)

        # handle screenshot action (triggered via button or TCP client)        
        if self.snapshot == True:
            exporter = pg.exporters.ImageExporter(self.vb.scene())  # store whole viewbox, including colorbar and ROIs
            #exporter.parameters()['width'] = 400   # export options (note width also affects height parameter)
            return_value = exporter.export(self.file_name)               
            #self.image.save(self.file_name)        # this would save only the image, without colorbar and ROIs
            if return_value == False:
                print("screenshot failed, could not write file. Does the directory exist? self.folder:", self.folder)
            self.snapshot=False

        data = {}
        # save temperature data of whole frame
        fmin_C = np.round(self.ktoc(raw_frame.min()),1)
        fmax_C = np.round(self.ktoc(raw_frame.max()),1)
        favg_C = np.round(self.ktoc(raw_frame.mean()),1)
        data["frame"] =  {"min.": fmin_C, "avg.": favg_C, "max.": fmax_C}

        # collect temperature data of ROIs
        for i,roi in enumerate(self.ROIs):
            roi_img = roi.getArrayRegion(scaled_frame, img=self.image,axes=(0,1))
            min_C = round(self.ktoc(roi_img.min()),1)
            max_C = round(self.ktoc(roi_img.max()),1)
            avg_C = round(self.ktoc(roi_img.mean()),1)
            data["  ROI "+str(i)] = {"min.": min_C, "avg.": avg_C, "max.": max_C}
            #print(roi_img)

        self.temperatures = data #table update via timer
        
        #self.raw_frame = raw_frame # save current raw frame for further processing, e.g. sending to TCP client?
        
        # resize colorbar to match current temperature range of whole frame
        self.cb.setLevels(low=fmin_C,high=fmax_C)
    
    def update_table(self):
            length=len(self.ROIs)
            for (i,(key,val)) in enumerate(self.temperatures.items()):
                #print (i,key, list(val.values()))
                self.table.setRow(i,list(val.values()))  
            for i in range(length+1,len(self.column_labels)):
                self.table.setRow(i,("","","","")) 
   
    def add_roi(self):
        if len(self.ROIs) < self.max_ROIs:
            roi = pg.RectROI([0,200], [640,200], \
                            pen=self.roi_pen, \
                            hoverPen=self.roi_hoverPen, \
                            removable=True) #, maxBounds=self.vb.itemBoundingRect(self.image))
            roi.addScaleRotateHandle([0, 0.5], [1, 0.5])
            roi.addScaleRotateHandle([1, 0.5], [0, 0.5])
            roi.addScaleHandle([0, 0], [1, 1])
            roi.setAcceptedMouseButtons(QtCore.Qt.MouseButton.LeftButton)
            #roi.sigClicked.connect(self.highlight_roi) # cannot really compare this to clicks outside of ROIs
            roi.sigHoverEvent.connect(self.highlight_roi)
            roi.sigRemoveRequested.connect(self.del_roi)

            self.vb.addItem(roi,ignoreBounds=False)
            self.ROIs.append(roi)
            length=len(self.ROIs)
            self.column_labels[length] = "  ROI "+str(length-1)
            self.table.setVerticalHeaderLabels(self.column_labels)
            self.table.verticalHeadersSet = True
            self.table.resizeColumnsToContents() 
            print("ROI added, total: ", len(self.ROIs))
            return roi
        else:
            print("Max. number of ROIs reached, cannot add more!")
            return None

    
    def highlight_roi(self,selected_roi):
        # simple hilghting of ROIs via "*" in row headers
        # unfortunately, there is no signal emitted when a ROI is un-hovered, 
        # so we cannot remove the highlight once the mouse leaves a ROI
        if len(self.ROIs) > 1:
            for i,roi in enumerate(self.ROIs):
                if roi is selected_roi:
                    self.highlighted_roi=i
            labels = self.column_labels.copy()
            labels[self.highlighted_roi+1] = "* ROI "+str(self.highlighted_roi)
            self.table.setVerticalHeaderLabels(labels) 
            self.table.verticalHeadersSet = True 
    
    def clear_highlighted_roi(self,item):
        # FIXME: not used at the moment
        # TODO: find some event that could trigger this
        self.table.setVerticalHeaderLabels(self.column_labels) 
        self.table.verticalHeadersSet = True 
        self.highlighted_roi = None

    def del_roi(self,remove_roi):
        if remove_roi == False:
            # delete ROI with highest index
            remove_roi = self.ROIs[-1]

        for i,roi in enumerate(self.ROIs):
            if roi is remove_roi:
                self.vb.removeItem(roi)
                self.ROIs.pop(i)
                del self.table.items[i+1]

        length=len(self.ROIs)
        for i in range(1,len(self.column_labels)):
            if i <= length:
                self.column_labels[i] = "  ROI "+str(i-1)
            else:
                self.column_labels[i] = ""
        self.table.setVerticalHeaderLabels(self.column_labels)
        self.table.verticalHeadersSet = True 
        print("ROI deleted, left: ", len(self.ROIs))

    def load_config(self):
        if len(self.ROIs) != 0:
            print("ROIs already present, will not load ROIs from config file before existing ones are deleted!")
            return
        else:
            print("loading general settings and ROIs from config file")
            with open('config.yaml', 'r') as yaml_file:    
                loaded = yaml.load(yaml_file,Loader=yaml.FullLoader)
                #print(loaded)
                self.folder = loaded["folder"]
                del loaded["folder"]
                self.ROIs = [] # reset to zero
                # remaining key/value pairs are considered ROIs
                for i,state in enumerate(loaded.items()):
                    # print(i,state)
                    roi=self.add_roi()
                    if roi is not None:
                        roi.setState(state[1])
                return

    def save_config(self):
        # construct settings dictionary
        d = {}
        d["folder"] = self.folder
        for i,roi in enumerate(self.ROIs):
            d["ROI " + str(i)] = roi.saveState()
        with open('config.yaml', 'w') as yaml_file:
            yaml.dump(d,yaml_file,default_flow_style=False,sort_keys=False)
        print("saving general settings and ROIs to config file")

    def change_colormap(self, colormap):
        self.colormap = colormap    # update_frame will apply the current colormap to the image
        cm = pg.colormap.getFromMatplotlib(colormap)
        self.cb.setColorMap(cm)     # apply colormap to colorbar

    def take_screenshot(self):
        self.prefix = self.prefix_input.text()
        now = datetime.datetime.now()
        fstring=now.strftime(str(self.folder) + "/" + self.prefix + self.postfix)
        self.file_name = fstring + self.image_format
        print("screenshot triggered by button, file name: ", self.file_name)
        self.snapshot = True    # signal update_frame() to actually take the screenshot
    
    def client_connected(self):
        # only allow one TCP connection for simplicity
        newClient = self.tcpServer.nextPendingConnection()
        if self.clientConnection is None:
            self.clientConnection = newClient
            self.clientConnection.disconnected.connect(self.client_disconnected)
            self.clientConnection.readyRead.connect(self.client_command)
            print("TCP client connected")
        else:
            print("Additional TCP connection request ignored, only one is supported!")    

    def client_disconnected(self):
        print("TCP client disconnected")
        self.clientConnection.deleteLater()
        self.clientConnection = None
    
    def client_command(self):
        # store whatever is received from TCP client as prefix string for the screenshot file name
        prefix = self.clientConnection.readAll()
        self.prefix = str(prefix, encoding='ascii').strip()
        self.prefix_input.setText(self.prefix)
        # create timestamp and complete file file name
        now = datetime.datetime.now()
        fstring=now.strftime(str(self.folder) + "/" + self.prefix + self.postfix)
        self.file_name = fstring + self.image_format
        print("screenshot triggered by TCP client, file name: ", self.file_name)
        self.snapshot = True    # signal update_frame() to actually take the screenshot
        # remove trailing spaces in keys
        stripped_temps = {key.strip(): value for key, value in self.temperatures.items()}
        reply_dict = { "file name" : self.file_name, **stripped_temps}
        # send back temperature values in degrees Celsius as JSON string
        temps = json.dumps(reply_dict, indent=1).encode('utf-8') #set ident zero to save some bytes :)
        self.clientConnection.write(temps)

    def get_image(self,data,scale):
        # scale and rotate image data
        data = cv2.resize(data.T, (scale*data.shape[0],scale*data.shape[1]), interpolation = cv2.INTER_NEAREST)
        # need to pass a copy here for keeping raw frame data unmodified
        img = self.raw_to_8bit(np.copy(data))
        return data,img
    
    def ktoc(self,val):
        # convert raw data (in Kelvin) to degrees Celsius
        return (val - 27315) / 100.0

    def ctok(self,val):
        # convert from degrees Celsius to raw data (in Kelvin)
        # usefull for creating simulated data
        return (val * 100) + 27315 

    def raw_to_8bit(self,data):
        # convert raw radiometric data format from FLIR camera
        # normalize considers 16bit unsigned data
        # 8 bit shift to fit the unsigned 8bit array format.
        cv2.normalize(data, data, 0, 65535, cv2.NORM_MINMAX)
        np.right_shift(data,8,data)
        return np.uint8(data)
    
    class ColormapSelector(QtWidgets.QComboBox):
        def __init__(self, parent=None, default_index=0) -> None:
            super().__init__(parent)
            self.parent = self.parent()

            # polpulate drop-down selecter with color maps
            self.addItems(self.parent.selectable_colormaps.keys())
            # show default colormap desciptor
            self.setCurrentIndex(default_index)
            self.currentIndexChanged.connect(self.on_index_changed)

        def on_index_changed(self, index):
            selected_colormap = self.itemText(index)
            self.parent.change_colormap(selected_colormap)
