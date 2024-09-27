import logging
import os
from typing import Annotated, Optional
import json
from datetime import datetime
import requests
import vtk
import qt
import slicer
import csv
from slicer.i18n import tr as _
from slicer.i18n import translate
from slicer.ScriptedLoadableModule import *
from slicer.util import VTKObservationMixin
from slicer.parameterNodeWrapper import (
    parameterNodeWrapper,
    WithinRange,
)

from slicer import vtkMRMLScalarVolumeNode


#
# SlicerTelemetry
#


def onUsageEventLogged(component, event):
    # Get the current date without hours and seconds
    event_day = datetime.now().strftime('%Y-%m-%d')
    event_info = {
        'component': component,
        'event': event,
        'day': event_day
    }
    print(f"Logged event: {component} - {event} on {event_day}")

    # Read existing data from the CSV file
    csv_file_path = 'telemetry_events.csv'
    event_counts = {}
    try:
        with open(csv_file_path, 'r', newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                key = (row['component'], row['event'], row['day'])
                event_counts[key] = int(row['times'])
    except FileNotFoundError:
        # File does not exist yet, so we start with an empty dictionary
        pass
    except Exception as e:
        print(f"Error reading CSV file: {e}")
        return

    # Update the count for the current event
    key = (component, event, event_day)
    if key in event_counts:
        event_counts[key] += 1
    else:
        event_counts[key] = 1

    # Save the updated counts back to the CSV file
    try:
        with open(csv_file_path, 'w', newline='') as csvfile:
            fieldnames = ['component', 'event', 'day', 'times']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for (component, event, day), times in event_counts.items():
                writer.writerow({'component': component, 'event': event, 'day': day, 'times': times})
    except Exception as e:
        print(f"Error saving event to CSV file: {e}")

# Connect to the usageEventLogged signal if usage logging is supported
if hasattr(slicer.app, 'usageEventLogged') and slicer.app.isUsageLoggingSupported:
    slicer.app.usageEventLogged.connect(onUsageEventLogged)

class SlicerTelemetry(ScriptedLoadableModule):
    """Uses ScriptedLoadableModule base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = _("SlicerTelemetry")  # TODO: make this more human readable by adding spaces
        # TODO: set categories (folders where the module shows up in the module selector)
        self.parent.categories = [translate("qSlicerAbstractCoreModule", "Telemetry")]
        self.parent.dependencies = []  # TODO: add here list of module names that this module requires
        self.parent.contributors = ["Dominguez Bernardo"]  # TODO: replace with "Firstname Lastname (Organization)"
        # TODO: update with short description of the module and a link to online module documentation
        # _() function marks text as translatable to other languages
        self.parent.helpText = _("""
This is an example of scripted loadable module bundled in an extension.
See more information in <a href="https://github.com/organization/projectname#MyFirstModule">module documentation</a>.
""")
        # TODO: replace with organization, grant and thanks
        self.parent.acknowledgementText = _("""
This file was originally developed by Jean-Christophe Fillion-Robin, Kitware Inc., Andras Lasso, PerkLab,
and Steve Pieper, Isomics, Inc. and was partially funded by NIH grant 3P41RR013218-12S1.
""")
        
        self.url = "http://127.0.0.1:8080/telemetry"
        self.headers = {"Content-Type": "application/json"}
        self.csv_file_path = 'telemetry_events.csv'

        self.loggedEvents = []
        self.urlsByReply = {}
        

        #load logging events from csv file
        try:
            with open(self.csv_file_path, 'r') as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    self.loggedEvents.append(row)
        except Exception as e:
            print(f"Error loading events from CSV file: {e}")

        slicer.app.connect("startupCompleted()", self.onStartupCompleted)
        
        try:
            import qt
            self.networkAccessManager = qt.QNetworkAccessManager()
            self.networkAccessManager.finished.connect(self.handleQtReply)
            self.http2Allowed = True
            self._haveQT = True
        except ModuleNotFoundError:
            self._haveQT = False

    def onStartupCompleted(self):
        qt.QTimer.singleShot(5000, self.showPopup)

    def showPopup(self):
        if self.shouldShowPopup():
            dialog = qt.QMessageBox()
            dialog.setWindowTitle("Telemetry")
            dialog.setText("Would you like to send telemetry data to the server? Click detailed text to see the data")
            dialog.setDetailedText(json.dumps(self.loggedEvents, indent=4))
            dialog.setStandardButtons(qt.QMessageBox.Yes | qt.QMessageBox.No | qt.QMessageBox.Cancel)
            dialog.setDefaultButton(qt.QMessageBox.Yes)
            checkbox = qt.QCheckBox("Do not ask again")
            dialog.setCheckBox(checkbox)

            response = dialog.exec_()
            do_not_ask_again = checkbox.isChecked()

            if do_not_ask_again:
                self.saveUserResponse(response)

            if response == qt.QMessageBox.Yes:
                print("User accepted the telemetry upload.")
                self.weeklyUsageUpload()
            elif response == qt.QMessageBox.No:
                print("User rejected the telemetry upload.")
            elif response == qt.QMessageBox.Cancel:
                print("User chose to be asked later.")

    def shouldShowPopup(self):
        settings = qt.QSettings()
        lastSent = settings.value("lastSent")
        try:
            if not lastSent:
                current_date = datetime.now().isoformat()
                lastSent = current_date
                settings.setValue("lastSent", current_date)                                            
        except Exception as e:
            print(f"Error loading last sent date from Qsettings: {e}")
        
        if lastSent:
            lastSentDate = datetime.fromisoformat(lastSent)
            if (datetime.now() - lastSentDate).days >= 7:
                settings = qt.QSettings()
                response = settings.value("TelemetryUserResponse")
                if response == 'yes':
                    self.weeklyUsageUpload()
                    return False
                elif response == 'no':
                    return False
                elif response == 'cancel':
                    return True
                elif response == None:
                    return True
            else:
                return False

    def saveUserResponse(self, response):
        print("Saving user response")
        settings = qt.QSettings()
        if response == qt.QMessageBox.Yes:
            settings.setValue("TelemetryUserResponse", "yes")
        elif response == qt.QMessageBox.No:
            settings.setValue("TelemetryUserResponse", "no")
        elif response == qt.QMessageBox.Cancel:
            settings.setValue("TelemetryUserResponse", "cancel")

    
    def weeklyUsageUpload(self):
        import qt
        settings = qt.QSettings()
        lastSent = settings.value("lastSent")
        try:
            if not lastSent:
                current_date = datetime.now().isoformat()
                lastSent = current_date
                settings.setValue("lastSent", current_date)
        except Exception as e:
            print(f"Error loading last sent date from Qsettings: {e}")
        
        if lastSent:
            lastSentDate = datetime.fromisoformat(lastSent)
            if (datetime.now() - lastSentDate).days >= 7:
                try:
                    data_to_send = self.loggedEvents 
                    if self._haveQT:
                        import qt
                        request = qt.QNetworkRequest(qt.QUrl(self.url))
                        request.setHeader(qt.QNetworkRequest.ContentTypeHeader, "application/json")
                        json_data = json.dumps(data_to_send)
                        if not data_to_send:
                            print("No logged events to send")
                            return
                        response = self.networkAccessManager.post(request, json_data.encode('utf-8'))
                        self.urlsByReply[response] = self.url
                    else:
                        response = requests.post(self.url, headers=self.headers, json=self.loggedEvents)
                        if response.status_code == 200:
                            print("Logged events sent to server")
                            self.loggedEvents.clear()
                            with open(self.csv_file_path, 'w') as csvfile:
                                csvfile.truncate()
                            with open(self.file_path, 'w') as file:
                                current_date = datetime.now().isoformat()
                                file.write(current_date)
                        else:
                            print(f"Error sending logged events to server: {response.status_code}")
                            print(f"Response content: {response.text}")                    
                except Exception as e:
                    print(f"Error sending logged events to server: {e}")



    def handleQtReply(self, reply):
        if reply.error() != qt.QNetworkReply.NoError:
            print(f"Error is {reply.error()}")
        url = self.urlsByReply.get(reply)
        if url:
            del self.urlsByReply[reply]
        content = reply.readAll().data()
        if reply.error() == qt.QNetworkReply.NoError:
            print("Logged events sent to server")
            settings = qt.QSettings()
            settings.setValue("lastSent", datetime.now().isoformat())
            with open(self.csv_file_path, 'w') as csvfile:
                csvfile.truncate()
        else:
            print(f"Error sending logged events to server: {reply.errorString()}")
        reply.deleteLater()



#
# SlicerTelemetryParameterNode
#

@parameterNodeWrapper
class SlicerTelemetryParameterNode:
    """
    The parameters needed by module.

    """


#
# SlicerTelemetryWidget
#


class SlicerTelemetryWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):
    """Uses ScriptedLoadableModuleWidget base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self, parent=None) -> None:
        """Called when the user opens the module the first time and the widget is initialized."""
        ScriptedLoadableModuleWidget.__init__(self, parent)
        VTKObservationMixin.__init__(self)  # needed for parameter node observation
        self.logic = None
        self._parameterNode = None
        self._parameterNodeGuiTag = None

    def setup(self) -> None:
        """Called when the user opens the module the first time and the widget is initialized."""
        ScriptedLoadableModuleWidget.setup(self)

        # Load widget from .ui file (created by Qt Designer).
        # Additional widgets can be instantiated manually and added to self.layout.
        uiWidget = slicer.util.loadUI(self.resourcePath("UI/SlicerTelemetry.ui"))
        self.layout.addWidget(uiWidget)
        self.ui = slicer.util.childWidgetVariables(uiWidget)

        # Set scene in MRML widgets. Make sure that in Qt designer the top-level qMRMLWidget's
        # "mrmlSceneChanged(vtkMRMLScene*)" signal in is connected to each MRML widget's.
        # "setMRMLScene(vtkMRMLScene*)" slot.
        uiWidget.setMRMLScene(slicer.mrmlScene)

        # Create logic class. Logic implements all computations that should be possible to run
        # in batch mode, without a graphical user interface.
        self.logic = SlicerTelemetryLogic()

        # Connections

        # These connections ensure that we update parameter node when scene is closed
        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.StartCloseEvent, self.onSceneStartClose)
        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.EndCloseEvent, self.onSceneEndClose)
       

        # Buttons
        self.ui.applyButton.connect("clicked(bool)", self.onApplyButton)

        # Make sure parameter node is initialized (needed for module reload)
        self.initializeParameterNode()

       
    

    def cleanup(self) -> None:
        """Called when the application closes and the module widget is destroyed."""
        self.removeObservers()

    def enter(self) -> None:
        """Called each time the user opens this module."""
        # Make sure parameter node exists and observed
        self.initializeParameterNode()

    def exit(self) -> None:
        """Called each time the user opens a different module."""
        # Do not react to parameter node changes (GUI will be updated when the user enters into the module)
        if self._parameterNode:
            self._parameterNode.disconnectGui(self._parameterNodeGuiTag)
            self._parameterNodeGuiTag = None
            self.removeObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._checkCanApply)

    def onSceneStartClose(self, caller, event) -> None:
        """Called just before the scene is closed."""
        # Parameter node will be reset, do not use it anymore
        self.setParameterNode(None)

    def onSceneEndClose(self, caller, event) -> None:
        """Called just after the scene is closed."""
        # If this module is shown while the scene is closed then recreate a new parameter node immediately
        if self.parent.isEntered:
            self.initializeParameterNode()

    def initializeParameterNode(self) -> None:
        """Ensure parameter node exists and observed."""
        # Parameter node stores all user choices in parameter values, node selections, etc.
        # so that when the scene is saved and reloaded, these settings are restored.

        self.setParameterNode(self.logic.getParameterNode())


    def setParameterNode(self, inputParameterNode: Optional[SlicerTelemetryParameterNode]) -> None:
        """
        Set and observe parameter node.
        Observation is needed because when the parameter node is changed then the GUI must be updated immediately.
        """


    def onApplyButton(self) -> None:
        """Run when user clicks "Apply" button."""
        try:
            self.logic.logAnEvent()
        except Exception as e:
            slicer.util.errorDisplay("Failed to send function count to server. See Python Console for error details.")
            import traceback
            traceback.print_exc()

                
#
# SlicerTelemetryLogic
#


class SlicerTelemetryLogic(ScriptedLoadableModuleLogic):
    """This class should implement all the actual
    computation done by your module.  The interface
    should be such that other python code can import
    this class and make use of the functionality without
    requiring an instance of the Widget.
    Uses ScriptedLoadableModuleLogic base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self) -> None:
        """Called when the logic class is instantiated. Can be used for initializing member variables."""
        ScriptedLoadableModuleLogic.__init__(self)


    
    def getParameterNode(self):
        return SlicerTelemetryParameterNode(super().getParameterNode())


    def logAnEvent(self):
        # Log this event
        if hasattr(slicer.app, 'logUsageEvent') and slicer.app.isUsageLoggingSupported:
            slicer.app.logUsageEvent("TelemetryExtension", "logAnEvent")


#
# SlicerTelemetryTest
#


class SlicerTelemetryTest(ScriptedLoadableModuleTest):
    """
    This is the test case for your scripted module.
    Uses ScriptedLoadableModuleTest base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def setUp(self):
        """Do whatever is needed to reset the state - typically a scene clear will be enough."""
        slicer.mrmlScene.Clear()

    def runTest(self):
        """Run as few or as many tests as needed here."""
        self.setUp()
        self.test_SlicerTelemetry1()

    def test_SlicerTelemetry1(self):
        """Ideally you should have several levels of tests.  At the lowest level
        tests should exercise the functionality of the logic with different inputs
        (both valid and invalid).  At higher levels your tests should emulate the
        way the user would interact with your code and confirm that it still works
        the way you intended.
        One of the most important features of the tests is that it should alert other
        developers when their changes will have an impact on the behavior of your
        module.  For example, if a developer removes a feature that you depend on,
        your test should break so they know that the feature is needed.
        """
