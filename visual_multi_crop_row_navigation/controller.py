# Copyright 2022 Agricultural-Robotics-Bonn
# All rights reserved.
#
# Software License Agreement (BSD 2-Clause Simplified License)
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following
#    disclaimer in the documentation and/or other materials provided
#    with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.


# Class to compute the control parameters 
import numpy as np


# Function to wrap an angle to the interval [-pi,pi]
def wrapToPi(theta):
    while theta < -np.pi:
        theta = theta + 2 * np.pi
    while theta > np.pi:
        theta = theta - 2 * np.pi
    return theta


# Function to compute the controls given the current state and the desired state and velocity
def visualServoingCtl(camera, desiredState, actualState, v_des):
    # specify the actual state for better readability
    x = actualState[0]
    y = actualState[1]
    theta = actualState[2]

    # some crazy parameters   
    lambda_x_1 = 10
    lambda_w_1 = 3000
    lambdavec = np.array([lambda_x_1, lambda_w_1])

    # state if it is a row or a column controller
    controller_type = 0

    # s_c = J * u = L_sc * T_r->c * u_r

    # Computation of the Interaction Matrix as presented by Cherubini & Chaumette relates the
    # control variables [v,w] in the camera frame to the change of the features [x,y,theta]
    angle = camera.tilt_angle
    delta_z = camera.deltaz
    IntMat = np.array([[(-np.sin(angle) - y * np.cos(angle)) / delta_z, 0,
                        x * (np.sin(angle) + y * np.cos(angle)) / delta_z, x * y, -1 - x ** 2, y],
                       [0, -(np.sin(angle) + y * np.cos(angle)) / delta_z,
                        y * (np.sin(angle) + y * np.cos(angle)) / delta_z, 1 + y ** 2, -x * y, -x],
                       [np.cos(angle) * np.power(np.cos(theta), 2) / delta_z,
                        np.cos(angle) * np.cos(theta) * np.sin(theta) / delta_z,
                        -(np.cos(angle) * np.cos(theta) * (
                                y * np.sin(theta) + x * np.cos(theta))) / delta_z,
                        -(y * np.sin(theta) + x * np.cos(theta)) * np.cos(theta),
                        -(y * np.sin(theta) + x * np.cos(theta)) * np.sin(theta), -1]])

    # Computation of the transformation from the robot to the camera frame
    delta_y = camera.deltay
    TransfMat = np.array([[0, -delta_y],
                          [-np.sin(angle), 0],
                          [np.cos(angle), 0],
                          [0, 0],
                          [0, -np.cos(angle)],
                          [0, -np.sin(angle)]])
    Trans_vel = TransfMat[:, 0]
    Trans_ang = TransfMat[:, 1]

    # Computation of the Jacobi-Matrix for velocity and angular velocity and their pseudo
    # inverse The Jacobi-Matrix relates the control variables in the robot frame to the change
    # of the features
    Jac = np.array([IntMat[controller_type, :], IntMat[2, :]])
    Jac_vel = np.matmul(Jac, Trans_vel)
    # Jac_vel_pi = np.linalg.pinv([Jac_vel])
    Jac_ang = np.matmul(Jac, Trans_ang)
    Jac_ang_pi = np.linalg.pinv([Jac_ang])

    # Compute the delta, in this case the difference between the actual state and the desired state
    trans_delta = actualState[controller_type] - desiredState[controller_type]
    ang_delta = actualState[2] - desiredState[2]
    delta = np.array([trans_delta, wrapToPi(ang_delta)])

    # Compute the feedback control for the angular velocity
    temp = lambdavec * delta
    ang_fb = np.matmul(-Jac_ang_pi.T, (temp + Jac_vel * v_des))
    return ang_fb
