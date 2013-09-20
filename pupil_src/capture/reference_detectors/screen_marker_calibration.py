import cv2
import numpy as np
from methods import normalize,denormalize
from gl_utils import draw_gl_point,draw_gl_point_norm,draw_gl_polyline, adjust_gl_view,clear_gl_screen
import OpenGL.GL as gl
from glfw import *
from OpenGL.GLU import gluOrtho2D
import calibrate
from ctypes import c_int,c_bool
import atb
import audio

from plugin import Plugin



def draw_circle(pos,r,c):
    pts = cv2.ellipse2Poly(tuple(pos),(r,r),0,0,360,10)
    draw_gl_polyline(pts,c,'Polygon')

def draw_marker(pos):
    pos = int(pos[0]),int(pos[1])
    black = (0.,0.,0.,1.)
    white = (1.,1.,1.,1.)
    for r,c in zip((50,40,30,20,10),(black,white,black,white,black)):
        draw_circle(pos,r,c)


# def calbacks
def on_resize(window,w, h):
    glfwMakeContextCurrent(window)
    adjust_gl_view(w,h)




class Screen_Marker_Calibration(Plugin):
    """Calibrate using a marker on your screen
    We use a ring detector that moves across the screen to 9 sites
    Points are collected at sites not between

    """
    def __init__(self, g_pool, atb_pos=(0,0)):
        Plugin.__init__(self)
        self.g_pool = g_pool
        self.active = False
        self.detected = False
        self.screen_marker_state = 0
        self.screen_marker_max = 70 # maximum bound for state
        self.active_site = 0
        self.sites = []
        self.display_pos = None
        self.on_position = False

        self.candidate_ellipses = []
        self.pos = None

        self.show_edges = c_bool(0)
        self.aperture = c_int(7)
        self.dist_threshold = c_int(5)
        self.area_threshold = c_int(20)

        self.world_size = None

        self.cal_window = None


        self.fullscreen = c_bool(1)
        self.monitor_idx = c_int(0)
        self.monitor_handles = glfwGetMonitors()
        self.monitor_names = [glfwGetMonitorName(m) for m in self.monitor_handles]
        monitor_enum = atb.enum("Monitor",dict(((key,val) for val,key in enumerate(self.monitor_names))))
        #primary_monitor = glfwGetPrimaryMonitor()



        atb_label = "calibrate on screen"
        # Creating an ATB Bar is required. Show at least some info about the Ref_Detector
        self._bar = atb.Bar(name = self.__class__.__name__, label=atb_label,
            help="ref detection parameters", color=(50, 50, 50), alpha=100,
            text='light', position=atb_pos,refresh=.3, size=(300, 100))
        self._bar.add_var("monitor",self.monitor_idx, vtype=monitor_enum)
        self._bar.add_var("fullscreen", self.fullscreen)
        self._bar.add_button("  start calibrating  ", self.start, key='c')

        self._bar.add_separator("Sep1")
        self._bar.add_var("show edges",self.show_edges)
        self._bar.add_var("aperture", self.aperture, min=3,step=2)
        self._bar.add_var("area threshold", self.area_threshold)
        self._bar.add_var("eccetricity threshold", self.dist_threshold)


    def start(self):
        if self.active:
            return

        audio.say("Starting Calibration")

        c = 1.
        self.sites = [  (.0, 0),
                        (-c,c), (0.,c),(c,c),
                        (c,0.),
                        (c,-c), (0., -c),( -c, -c),
                        (-c,0.),
                        (.0,0.),(.0,0.)]

        self.active_site = 0
        self.active = True
        self.ref_list = []
        self.pupil_list = []


        if self.fullscreen.value:
            monitor = self.monitor_handles[self.monitor_idx.value]
            mode = glfwGetVideoMode(monitor)
            height,width= mode[0],mode[1]
        else:
            monitor = None
            height,width= 640,360

        self.cal_window = glfwCreateWindow(height, width, "Calibration", monitor=monitor, share=None)
        if not self.fullscreen.value:
            glfwSetWindowPos(self.cal_window,200,0)

        on_resize(self.cal_window,height,width)

        #Register cllbacks
        glfwSetWindowSizeCallback(self.cal_window,on_resize)
        glfwSetWindowCloseCallback(self.cal_window,self.on_stop)
        glfwSetKeyCallback(self.cal_window,self.on_key)
        # glfwSetCharCallback(self.cal_window,on_char)

    def on_key(self,window, key, scancode, action, mods):
        if not atb.TwEventKeyboardGLFW(key,int(action == GLFW_PRESS)):
            if action == GLFW_PRESS:
                if key == GLFW_KEY_ESCAPE:
                    self.stop()

    def on_stop(self,window):
        self.stop()

    def stop(self):
        audio.say("Stopping Calibration")
        self.screen_marker_state = 0
        self.active = False

        glfwDestroyWindow(self.cal_window)
        self.cal_window = None

        print len(self.pupil_list), len(self.ref_list)
        cal_pt_cloud = calibrate.preprocess_data(self.pupil_list,self.ref_list)

        print "Collected ", len(cal_pt_cloud), " data points."

        if len(cal_pt_cloud) < 20:
            print "Did not collect enough data."
            return

        cal_pt_cloud = np.array(cal_pt_cloud)

        self.g_pool.map_pupil = calibrate.get_map_from_cloud(cal_pt_cloud,self.world_size,verbose=True)
        np.save('cal_pt_cloud.npy',cal_pt_cloud)





    def update(self,frame,recent_pupil_positions):
        if self.active:
            img = frame.img


            #detect the marker
            gray_img = cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
            # self.candidate_points = self.detector.detect(s_img)

            # get threshold image used to get crisp-clean edges
            edges = cv2.adaptiveThreshold(gray_img, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, self.aperture.value, 7)
            # cv2.flip(edges,1 ,dst = edges,)
            # display the image for debugging purpuses
            # img[:] = cv2.cvtColor(edges,cv2.COLOR_GRAY2BGR)
            contours, hierarchy = cv2.findContours(edges,
                                            mode=cv2.RETR_TREE,
                                            method=cv2.CHAIN_APPROX_NONE,offset=(0,0)) #TC89_KCOS

            # remove extra encapsulation
            hierarchy = hierarchy[0]
            # turn outmost list into array
            contours =  np.array(contours)
            # keep only contours                        with parents     and      children
            contained_contours = contours[np.logical_and(hierarchy[:,3]>=0, hierarchy[:,2]>=0)]
            # turn on to debug contours
            if self.show_edges.value:
                cv2.drawContours(img, contained_contours,-1, (0,0,255))

            # need at least 5 points to fit ellipse
            contained_contours =  [c for c in contained_contours if len(c) >= 5]

            ellipses = [cv2.fitEllipse(c) for c in contained_contours]
            self.candidate_ellipses = []


            # filter for ellipses that have similar area as the source contour
            for e,c in zip(ellipses,contained_contours):
                a,b = e[1][0]/2.,e[1][1]/2.
                if abs(cv2.contourArea(c)-np.pi*a*b) <self.area_threshold.value:
                    self.candidate_ellipses.append(e)


            def man_dist(e,other):
                return abs(e[0][0]-other[0][0])+abs(e[0][1]-other[0][1])

            def get_cluster(ellipses):
                # retrun the first cluser of at least 3 concetric ellipses
                for e in ellipses:
                    close_ones = []
                    for other in ellipses:
                        if man_dist(e,other)<self.dist_threshold.value:
                            close_ones.append(other)
                    if len(close_ones)>=4:
                        # sort by major axis to return smallest ellipse first
                        close_ones.sort(key=lambda e: max(e[1]))
                        return close_ones
                return []

            self.candidate_ellipses = get_cluster(self.candidate_ellipses)



            if len(self.candidate_ellipses) > 0:
                self.detected= True
                marker_pos = self.candidate_ellipses[0][0]
                self.pos = normalize(marker_pos,(img.shape[1],img.shape[0]),flip_y=True)

            else:
                self.detected = False
                self.pos = None #indicate that no reference is detected


            #only save a valid ref position if within sample window of calibraiton routine
            on_position = 0 < self.screen_marker_state < self.screen_marker_max-50
            if on_position and self.detected:
                ref = {}
                ref["norm_pos"] = self.pos
                ref["timestamp"] = frame.timestamp
                self.ref_list.append(ref)

            #always save pupil positions
            for p_pt in recent_pupil_positions:
                if p_pt['norm_pupil'] is not None:
                    self.pupil_list.append(p_pt)

            # Animate the screen marker
            if self.screen_marker_state < self.screen_marker_max:
                if self.detected or not on_position:
                    self.screen_marker_state += 1
            else:
                self.screen_marker_state = 0
                self.active_site += 1
                print self.active_site
                if self.active_site == 10:
                    self.world_size = img.shape[1],img.shape[0]
                    self.stop()
                    return

            # function to smoothly interpolate between points input:(0-screen_marker_max) output: (0-1)
            m, s = self.screen_marker_max, self.screen_marker_state

            interpolation_weight = np.tanh(((s-2/3.*m)*4.)/(1/3.*m))*(-.5)+.5

            #use np.arrays for per element wise math
            current = np.array(self.sites[self.active_site])
            next = np.array(self.sites[self.active_site+1])
            # weighted sum to interpolate between current and next
            new_pos =  current * interpolation_weight + next * (1-interpolation_weight)
            #broadcast next commanded marker postion of screen
            self.display_pos = list(new_pos)
            self.on_position = on_position




    def gl_display(self):
        """
        use gl calls to render
        at least:
            the published position of the reference
        better:
            show the detected postion even if not published
        """

        if self.active and self.detected:
            for e in self.candidate_ellipses:
                pts = cv2.ellipse2Poly( (int(e[0][0]),int(e[0][1])),
                                    (int(e[1][0]/2),int(e[1][1]/2)),
                                    int(e[-1]),0,360,15)
                draw_gl_polyline(pts,(0.,1.,0,1.))
        else:
            pass
        if self.cal_window:
            self.gl_display_cal_window()


    def gl_display_cal_window(self):
        active_window = glfwGetCurrentContext()
        glfwMakeContextCurrent(self.cal_window)

        clear_gl_screen()

        # Set Matrix unsing gluOrtho2D to include padding for the marker of radius r
        #
        ############################
        #            r             #
        # 0,0##################w,h #
        # #                      # #
        # #                      # #
        #r#                      #r#
        # #                      # #
        # #                      # #
        # 0,h##################w,h #
        #            r             #
        ############################
        r = 60
        gl.glMatrixMode(gl.GL_PROJECTION)
        gl.glLoadIdentity()
        p_window_size = glfwGetWindowSize(self.cal_window)
        # compensate for radius of marker
        gluOrtho2D(-r,p_window_size[0]+r,p_window_size[1]+r, -r) # origin in the top left corner just like the img np-array
        # Switch back to Model View Matrix
        gl.glMatrixMode(gl.GL_MODELVIEW)
        gl.glLoadIdentity()

        screen_pos = denormalize(self.display_pos,p_window_size,flip_y=True)

        draw_marker(screen_pos)
        #some feedback on the detection state

        if self.detected and self.on_position:
            draw_gl_point(screen_pos, 5.0, (0.,1.,0.,1.))
        else:
            draw_gl_point(screen_pos, 5.0, (1.,0.,0.,1.))

        glfwSwapBuffers(self.cal_window)
        glfwMakeContextCurrent(active_window)


    def cleanup(self):
        """gets called when the plugin get terminated.
           either volunatily or forced.
        """
        self._bar.destroy()
        if self.active:
            self.stop()




