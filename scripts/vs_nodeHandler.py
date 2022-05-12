#!/usr/bin/env python2
# -*- coding: utf-8 -*-
from __future__ import division
from __future__ import print_function
from future.builtins import input

import rospy
import math
import cv2 as cv
import Camera as cam
import featureExtractor as fex
import Controller as vs_controller
import numpy as np
import time

from geometry_msgs.msg import Twist
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import itertools



class vs_nodeHandler:
    
    def __init__(self):

        # subscribed Topics (Images of front and back camera)
        front_topic = rospy.get_param('front_color_topic')
        back_topic = rospy.get_param('back_color_topic')
        self.sub_front_img = rospy.Subscriber(front_topic, Image, self.front_camera_callback, queue_size=1) 
        self.sub_back_img = rospy.Subscriber(back_topic, Image, self.back_camera_callback, queue_size=1) 

        # Initialize ros publisher, ros subscriber, topics we publish
        self.graphic_pub = rospy.Publisher('vs_nav/graphic',Image, queue_size=1)
        self.mask_pub = rospy.Publisher('vs_nav/mask',Image, queue_size=1)
        self.exg_pub = rospy.Publisher('vs_nav/ExG',Image, queue_size=1)

        cmd_vel_topic = rospy.get_param('cmd_vel_topic')
        self.velocity_pub = rospy.Publisher(cmd_vel_topic, Twist, queue_size=1)

        # cv bridge
        self.bridge = CvBridge()
        # input image resize ratio
        self.imgResizeRatio = rospy.get_param('imgResizeRatio')
        # settings
        # Mode 1: Driving forward with front camera (starting mode)
        # Mode 2: Driving forward with back camera
        # Mode 3: Driving backwards with back camera
        # Mode 4: Driving backwards with front camera
        self.navigationMode = rospy.get_param('navigationMode')                   
        # debug mode without publishing velocities 
        self.stationaryDebug = rospy.get_param('stationaryDebug')
        # speed limits
        self.omegaScaler = rospy.get_param('omegaScaler')
        self.maxOmega = rospy.get_param('maxOmega')
        self.minOmega = rospy.get_param('minOmega')
        self.maxLinearVel = rospy.get_param('maxLinearVel')
        self.minLinearVel = rospy.get_param('minLinearVel')
        # direction of motion 1: forward, -1:backward
        if self.navigationMode == 1 or self.navigationMode == 2:
            self.linearMotionDir = 1
        else:
            self.linearMotionDir = -1
        #  used to recoder direction of motion
        self.omegaBuffer = list()
        # true: front camera, False: back camera
        if self.isUsingFrontCamera(): 
            self.primaryCamera = True
        else:
            self.primaryCamera = False
        # switch process control parameters and settings
        self.switchingMode = False
        # buffer of detected keypoints to match for finding new rows
        self.matchingKeypoints = []
        #  buffer of descriptor of detected keypoints
        self.featureDescriptors = []
        #  number of lines to pass while lane switching 
        # (this gets automoatically initialized basedon detected line too)
        self.linesToPass = rospy.get_param('lines_to_pass')  
        # crop row recognition Difference thersholds
        self.max_matching_dif_features = rospy.get_param('max_matching_dif_features')         
        self.min_matching_dif_features = rospy.get_param('min_matching_dif_features')           
        # Threshold for keypoints
        self.matching_keypoints_th = rospy.get_param('matching_keypoints_th')
        # if there is no plant in the image
        self.noPlantsSeen = False
        self.nrNoPlantsSeen = 0
        # 
        self.windowProp = {
            "winSweepStart": rospy.get_param('winSweepStart'),
            "winSweepEnd": rospy.get_param('winSweepEnd'),
            "winMinWidth": rospy.get_param('winMinWidth'),
            "winSize": rospy.get_param('winSize')
        }
        #  in case of using bigger size image size, we suggest to set ROI 
        self.rioProp = {
            "enable_roi": rospy.get_param('enable_roi'),
            "p1": rospy.get_param('p1'),
            "p2": rospy.get_param('p2'),
            "p3": rospy.get_param('p3'),
            "p4": rospy.get_param('p4'),
            "p5": rospy.get_param('p5'),
            "p6": rospy.get_param('p6'),
            "p7": rospy.get_param('p7'),
            "p8": rospy.get_param('p8')
        }

        self.fexProp = {
            "min_contour_area": rospy.get_param('min_contour_area'),
            "max_coutour_height": rospy.get_param('max_coutour_height')
        }
        
        # images
        self.primary_img = []
        self.frontImg = None
        self.backImg = None

        self.velocityMsg = Twist()
        self.enoughPoints = True

        # camera
        self.camera = cam.Camera(1,1.2,0,1,np.deg2rad(-80),0.96,0,0,1)
        self.imageProcessor = fex.featureExtractor(self.windowProp, self.rioProp, self.fexProp)

        rospy.loginfo("#[VS] navigator initialied ... ")
        

    # main function to guide the robot through crop rows
    def navigate(self):
        # get the currently used image
        primaryImg = self.getProcessingImage(self.frontImg, self.backImg)    
        # If the feature extractor is not initialized yet, this has to be done
        if self.imageProcessor.isInitialized == False:
            print("Initialize image processor unit...")
            self.imageProcessor.initialize()
        
        # this is only False if the initialization in 'setImage' was unsuccessful
        if self.imageProcessor.isInitialized == False:
            rospy.logwarn("The initialization was unsuccessful!! ")
            # switch cameras
            self.getProcessingImage(self.frontImg, self.backImg, switchCamera=True)
            
        else:  
            # if the robot is currently following a line and is not turning just compute the controls
            if not self.switchingMode:
                validPathFound, ctlCommands = self.computeControls()
                
                # if the validPathFound is False (no lines are found)
                if not validPathFound:
                    rospy.logwarn("no lines are found !! validPathFound is False")
                    # switch to next mode
                    self.updateNavigationStage()
                    # if the mode is 2 or 4 one just switches the camera
                    if self.isExitingLane():
                        self.imageProcessor = fex.featureExtractor(self.windowProp, self.rioProp, self.fexProp)
                        self.getProcessingImage(self.frontImg, self.backImg, switchCamera=True)
                        self.navigate()
                    # if the mode is 1 or 3 the robot follows rows 
                    else:
                        print("#[INF] Turning Mode Enabled!!")
                        self.switchingMode = True
                        self.velocityMsg = Twist()
                        self.velocityMsg.linear.x = 0.0
                        self.velocityMsg.linear.y = 0.0
                        self.velocityMsg.angular.z = 0.0
                        time.sleep(1.0)
                        # Compute the features for the turning and stop the movement
                        self.imageProcessor.detectTrackingFeatures(self.navigationMode)
                        time.sleep(1.0)
                else:
                    self.velocityMsg.linear.x = ctlCommands[0]
                    self.velocityMsg.linear.y = 0.0
                    self.velocityMsg.angular.z = ctlCommands[1]
            # if the turning mode is enabled
            else: 
                # test if the condition for the row switching is fulfilled
                newLaneFound, graphic_img = self.imageProcessor.matchTrackingFeatures(self.navigationMode)
                if newLaneFound:
                    # the turn is completed and the new lines to follow are computed
                    self.navigationMode = fex.FeatureExtractor(self.windowProp, self.rioProp, self.fexProp)
                    self.switchDirection()
                    self.switchingMode = False
                    self.velocityMsg = Twist()
                    self.velocityMsg.linear.x = 0.07 * self.linearMotionDir
                    self.velocityMsg.linear.y = 0.0
                    self.velocityMsg.angular.z = 0.0
                    print("#[INF] Turning Mode disabled, entering next rows")
                else:
                    # if the condition is not fulfilled the robot moves continouisly sidewards
                    self.velocityMsg = Twist()
                    self.velocityMsg.linear.x = 0.0
                    self.velocityMsg.linear.y = -0.08
                    self.velocityMsg.angular.z = 0.0
                    print("#[INF] Side motion to find New Lane ...")
        
        if not self.stationaryDebug:
            # publish the commands to the robot
            if self.velocityMsg is not None:
                self.velocity_pub.publish(self.velocityMsg)

        print("#[INF] m:", 
              self.navigationMode, 
              "p-cam:", "front" if self.primaryCamera else "back", 
              "vel-x,y,z",
              self.velocityMsg.linear.x, 
              self.velocityMsg.linear.y, 
              round(self.velocityMsg.angular.z, 3),
              self.linearMotionDir,
              self.rotationDir)
        # Publish the Graphics image
        self.imageProcessor.drawGraphics()
        graphic_img = self.bridge.cv2_to_imgmsg(self.imageProcessor.processedIMG, encoding='rgb8')
        self.graphic_pub.publish(graphic_img)
        # publish predicted Mask
        mask_msg = CvBridge().cv2_to_imgmsg(self.mask)
        mask_msg.header.stamp = rospy.Time.now()
        self.mask_pub.publish(mask_msg)
        # publish Exg image 
        exg_msg = CvBridge().cv2_to_imgmsg(self.imageProcessor.greenIDX)
        exg_msg.header.stamp = rospy.Time.now()
        self.exg_pub.publish(exg_msg)
    
    # Function to deal with the front image
    def front_camera_callback(self, data):
        # get and set new image from the ROS topic
        self.frontImg = self.bridge.imgmsg_to_cv2(data, desired_encoding='rgb8')
        # get image size
        self.imgHeight, self.imgWidth, self.imgCh = self.frontImg.shape
        # set update props
        self.imageProcessor.setImgProp(primaryImg)
        # if the image is not empty
        if self.frontImg is not None and self.backImg is not None:
            # compute and publish robot controls if the image is currently used
            if self.primaryCamera:
                self.navigate()
                       
    # Function to deal with the back image
    def back_camera_callback(self, data):
        # get and set new image from the ROS topic
        self.backImg = self.bridge.imgmsg_to_cv2(data, desired_encoding='rgb8')
        # get image size
        self.imgHeight, self.imgWidth, self.imgCh = self.backImg.shape
        # if the image is not empty
        if self.frontImg is not None and self.backImg is not None:
            # compute and publish robot controls if the image is currently used
            if not self.primaryCamera:
                self.navigate()

    # updates navigation stagte (one higher or reset to 1 from > 4)
    def updateNavigationStage(self):
        self.navigationMode += 1
        if self.navigationMode > 4:
            self.navigationMode = 1  
        inputKey = input("#[INF] Press Enter to continue with mode:")
        print("#[INF] Switched to mode ", self.navigationMode)
    
    # condition of line existing action, modes 2, 4
    def isExitingLane(self):
        if self.navigationMode == 2 or self.navigationMode == 4:
            return True
        else:
            return False
    
    # if following a lane, modes 1, 3
    def isFollowingLane(self):
        if self.navigationMode == 1 or self.navigationMode == 3:
            return True
        else:
            return False
    
    def isUsingFrontCamera(self):
        if self.navigationMode == 1 or self.navigationMode == 4:
            return True
        else:
            return False
    
    def isUsingBackCamera(self):
        if self.navigationMode == 2 or self.navigationMode == 3:
            return True
        else: 
            return False
    
    # Function to manage the control variable for the driving direction
    def switchDirection(self):
        self.linearMotionDir = -self.linearMotionDir
        print("#####################switched Direction of Motion ...", self.linearMotionDir)
    
    # Function to manage the control variable for the driving rotation
    def switchRotationDir(self):
        self.rotationDir = -self.rotationDir
        print("&&&&&&&&&&&&&&&&&&&&&switched Direction of Rotation ...", self.rotationDir)

    # Function to set the currently used image
    def getProcessingImage(self, frontImg, backImg, switchCamera=False):
        if switchCamera:
            print("switch camera to the other ...")
            # Function to manage the switching between the two cameras
            self.primaryCamera = not self.primaryCamera
        # The front image is used
        if self.primaryCamera:
            primaryImg = frontImg
        # The back image is used
        else:
            primaryImg = backImg
        return primaryImg     
    
    # Function to compute the controls when following a crop row
    def computeControls(self):
        # extract features via the feature extractor
        lineFound, self.mask = self.imageProcessor.updateLinesAtWindows()
        # if features are found and the end of the row is not reached yet
        if lineFound: 
            # print("line found -- compute controls...")
            # extract the features
            x = self.imageProcessor.P[0]
            y = self.imageProcessor.P[1]
            t = self.imageProcessor.ang
            # define desired and actual feature vector
            desiredFeature = np.array([0.0, self.imgWidth/2, 0.0])
            actualFeature = np.array([x, y, t])
            # compute controls
            controls = vs_controller.Controller(self.camera, 
                                                desiredFeature, 
                                                actualFeature, 
                                                self.maxLinearVel)
            if self.isExitingLane():
                self.rotationDir = -1
            else:
                self.rotationDir = 1
            # scale rotational velocity 
            omega = self.omegaScaler * controls 
            # set linear speed and direction
            rho = 0.2 * self.linearMotionDir
            # store the command in a cache
            self.omegaBuffer.append(omega)
            
            return True, [rho, omega]
        
        # if no lines are found or the end of the row is reached
        else:
            print("No line found -- End of Row")
            # using the last known control 
            if len(self.omegaBuffer) == 0:
                self.omegaBuffer.append(0.0)
            # straight exit
            omega = 0.0
            rho = 0.05 * self.linearMotionDir
            return False, [rho, omega]

