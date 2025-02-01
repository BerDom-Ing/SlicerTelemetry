from datetime import datetime
import csv
import json

import requests
import qt
import slicer
from slicer import qSlicerWebWidget 
from slicer.i18n import tr as _
from slicer.i18n import translate
from slicer.ScriptedLoadableModule import (
    ScriptedLoadableModule,
    ScriptedLoadableModuleLogic,
    ScriptedLoadableModuleTest,
    ScriptedLoadableModuleWidget,
)


#
# Telemetry
#


def onUsageEventLogged(component, event):
    # Load settings
    settings = qt.QSettings()
    enabledExtensions = list(settings.value("enabledExtensions", []))
    disabledExtensions = list(settings.value("disabledExtensions", []))
    defaultExtensions = list(settings.value("defaultExtensions", []))
    telemetryDefaultPermission = settings.value("TelemetryDefaultPermission")
    
    # Check if the component should be logged
    if component in enabledExtensions:
        should_log = True
    elif component in disabledExtensions:
        should_log = False
    elif component in defaultExtensions:
        should_log = telemetryDefaultPermission
    else:
        should_log = False

    if not should_log:
        print(f"Component {component} is not in the enabled or default extensions with permission. Event not logged.")
        return

    # Get the current date without hours and seconds
    event_day = datetime.now().strftime('%Y-%m-%d')
    print(f"Logged event: {component} - {event} on {event_day}")

    # Read existing data from the CSV file
    csv_file_path = 'telemetry_events.csv'
    logged_events = TelemetryLogic.readLoggedEventsFromFile(csv_file_path)
    event_counts = {(row["component"], row["event"], row["day"]): int(row["times"]) for row in logged_events}

    # Update the count for the current event
    key = (component, event, event_day)
    event_counts[key] = event_counts.get(key, 0) + 1

    # Update logged events list
    logged_events = [
        {"component": component, "event": event, "day": day, "times": times}
        for (component, event, day), times in event_counts.items()]

    # Save the updated counts back to the CSV file
    TelemetryLogic.saveLoggedEventsToFile(csv_file_path, logged_events)

# Connect to the usageEventLogged signal if usage logging is supported
if hasattr(slicer.app, 'usageEventLogged') and slicer.app.isUsageLoggingSupported:
    slicer.app.usageEventLogged.connect(onUsageEventLogged)


class Telemetry(ScriptedLoadableModule):
    """Uses ScriptedLoadableModule base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = _("Telemetry")  # TODO: make this more human readable by adding spaces
        # TODO: set categories (folders where the module shows up in the module selector)
        self.parent.categories = [translate("qSlicerAbstractCoreModule", "Telemetry")]
        self.parent.dependencies = []  # TODO: add here list of module names that this module requires
        self.parent.contributors = ["Dominguez Bernardo"]  # TODO: replace with "Firstname Lastname (Organization)"
        # TODO: update with short description of the module and a link to online module documentation
        # _() function marks text as translatable to other languages
        self.parent.helpText = _("""
This extension allows 3D Slicer extensions to gather information on what software features are used. This information helps demonstrating impact, which is essential for getting continuous funding for maintenance and improvements.
""")
        self.parent.acknowledgementText = _("""
Bernardo Dominguez developed this module for his professional supervised practices of engineering studies at UTN-FRRO under the supervision and advice of PhD. Andras Lasso at Perklab and guidance from Slicer core developers""")
        
        self.url = "http://127.0.0.1:8080/telemetry"
        self.headers = {"Content-Type": "application/json"}
        self.csv_file_path = 'telemetry_events.csv'
        self.webWidget = None
        self.loggedEvents = []
        self.urlsByReply = {}

        # Load logging events from csv file
        self.loggedEvents = TelemetryLogic.readLoggedEventsFromFile(self.csv_file_path)

        slicer.app.connect("startupCompleted()", self.onStartupCompleted)
        
        try:
            import qt
            self.networkAccessManager = qt.QNetworkAccessManager()
            self.networkAccessManager.finished.connect(self.handleQtReply)
            self.http2Allowed = True
            self._haveQT = True
        except ModuleNotFoundError:
            self._haveQT = False
    
        slicer.app.extensionsManagerModel().extensionInstalled.connect(self.onExtensionInstalled)

    def onStartupCompleted(self):
        qt.QTimer.singleShot(4000, self.showTelemetryPermissionPopup)
        qt.QTimer.singleShot(5000, self.showPopup)

    def showTelemetryPermissionPopup(self):
        settings = qt.QSettings()
        if settings.value("TelemetryDefaultPermission", None) is None:
            dialog = qt.QMessageBox()
            dialog.setWindowTitle("Telemetry Permission")
            dialog.setText("Allow new installed extensions to enable telemetry?\n"
               "You can change this anytime in the telemetry extension")
            dialog.setStandardButtons(qt.QMessageBox.Ok)
            dialog.setDefaultButton(qt.QMessageBox.Ok)
            checkbox = qt.QCheckBox("Allow")
            dialog.setCheckBox(checkbox)

            response = dialog.exec_()
            allow_telemetry = checkbox.isChecked()

            if response == qt.QMessageBox.Ok and allow_telemetry:
                settings.setValue("TelemetryDefaultPermission", True)
            else:
                settings.setValue("TelemetryDefaultPermission", False)
    
    def onExtensionInstalled(self, extensionName):
        settings = qt.QSettings()
        telemetryDefaultPermission = settings.value("TelemetryDefaultPermission")
        print(f"Telemetry default permission: {telemetryDefaultPermission}")
        
        defaultExtensions = list(settings.value("defaultExtensions", []))
        
        if extensionName not in defaultExtensions:
            defaultExtensions.append(extensionName)
            settings.setValue("defaultExtensions", defaultExtensions)

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

            statsButton = dialog.addButton("Show Stats Dashboard", qt.QMessageBox.ActionRole)
            statsButton.clicked.connect(self.showStatsDashboard)

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


    def showStatsDashboard(self):
        if self.webWidget is None:
            self.webWidget = qSlicerWebWidget()

        events_json = json.dumps(self.loggedEvents)
        js_title_formats = {
            'time': r".title(d => `${d3.timeFormat('%Y-%m-%d')(d.key)}: ${d.value} events`)",
            'module': r".title(d => `${d.key}: ${d.value} events`)",
            'event': r".title(d => `${d.key}: ${d.value} occurrences`)"
        }
        
        htmlContent = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Slicer Usage Statistics</title>
            <style>
                body {{
                    background-color: white;
                    margin: 0;
                    padding: 0;
                }}
                .chart-title {{
                    font-weight: bold;
                    text-align: center;
                    margin: 10px 0;
                }}
                .dc-chart {{
                    margin-bottom: 30px;
                }}
                .chart-container {{
                    padding: 20px;
                }}
            </style>
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/dc/4.2.7/style/dc.min.css">
            <script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js"></script>
            <script src="https://cdnjs.cloudflare.com/ajax/libs/crossfilter2/1.5.4/crossfilter.min.js"></script>
            <script src="https://cdnjs.cloudflare.com/ajax/libs/dc/4.2.7/dc.min.js"></script>
            <script src="https://cdnjs.cloudflare.com/ajax/libs/d3-scale-chromatic/1.5.0/d3-scale-chromatic.min.js"></script>
        </head>
        <body>
        
            <div class="chart-container">
                <div class="chart-title">Event Timeline</div>
                <div id="time-chart"></div>
                
                <div class="chart-title">Module Usage</div>
                <div id="module-chart"></div>
                
                <div style="display: flex; justify-content: space-between;">
                    <div style="width: 48%;">
                        <div class="chart-title">Event Types</div>
                        <div id="event-chart"></div>
                    </div>
                </div>
            </div>
            
            <script>
                const loggedEvents = {events_json};
                
                function initializeStatsDashboard(loggedEvents) {{
                    const parseDate = d3.timeParse("%Y-%m-%d");
                    let expandedData = [];
                    
                    loggedEvents.forEach(d => {{
                        d.date = parseDate(d.day);
                        const times = parseInt(d.times, 10) || 1;
                        for (let i = 0; i < times; i++) {{
                            expandedData.push({{ ...d }});
                        }}
                    }});
                    
                    const cf = crossfilter(expandedData);
                    const dateDim = cf.dimension(d => d.date);
                    const moduleDim = cf.dimension(d => d.component);
                    const eventDim = cf.dimension(d => d.event);
                    const dateGroup = dateDim.group(d3.timeDay);
                    const moduleGroup = moduleDim.group().reduceCount();
                    const eventGroup = eventDim.group().reduceCount();
                    
                    // Update the color scheme
                    dc.config.defaultColors(d3.schemeCategory10);
                    
                    const timeChart = dc.barChart("#time-chart");
                    const moduleChart = dc.barChart("#module-chart");
                    const eventChart = dc.rowChart("#event-chart");
                    
                    timeChart
                        .width(900)
                        .height(200)
                        .margins({{top: 10, right: 10, bottom: 20, left: 40}})
                        .dimension(dateDim)
                        .group(dateGroup)
                        .x(d3.scaleTime().domain(d3.extent(expandedData, d => d.date)))
                        .round(d3.timeDay.round)
                        .xUnits(d3.timeDays)
                        .elasticY(true)
                        .renderHorizontalGridLines(true)
                        .brushOn(true)
                        {js_title_formats['time']};
                    
                    moduleChart
                        .width(900)
                        .height(300)
                        .margins({{top: 20, right: 20, bottom: 100, left: 40}})
                        .dimension(moduleDim)
                        .group(moduleGroup)
                        .x(d3.scaleBand())
                        .xUnits(dc.units.ordinal)
                        .elasticY(true)
                        .ordering(d => -d.value)
                        .renderHorizontalGridLines(true)
                        {js_title_formats['module']}
                        .on('renderlet', function(chart) {{
                            chart.selectAll('g.x text')
                                .attr('transform', 'translate(-10,10) rotate(270)')
                                .style('text-anchor', 'end');
                        }});
                    
                    eventChart
                        .width(400)
                        .height(300)
                        .margins({{top: 20, left: 10, right: 10, bottom: 20}})
                        .dimension(eventDim)
                        .group(eventGroup)
                        .elasticX(true)
                        .ordering(d => -d.value)
                        {js_title_formats['event']}
                        .on('renderlet', function(chart) {{
                            chart.selectAll('g.row text')
                                .style('fill', 'black');
                        }});
                    
                    dc.renderAll();
                }}
                
                initializeStatsDashboard(loggedEvents);
            </script>
        </body>
        </html>
        """

        self.webWidget.setHtml(htmlContent)
        self.webWidget.show()
        self.webWidget.setMinimumWidth(960)

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
                            TelemetryLogic.clearLoggedEventsFile(self.csv_file_path)
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
            TelemetryLogic.clearLoggedEventsFile(self.csv_file_path)
        else:
            print(f"Error sending logged events to server: {reply.errorString()}")
        reply.deleteLater()


#
# TelemetryWidget
#


class TelemetryWidget(ScriptedLoadableModuleWidget):

    def __init__(self, parent=None) -> None:
        """Called when the user opens the module the first time and the widget is initialized."""
        ScriptedLoadableModuleWidget.__init__(self, parent)
        self.logic = None

    def setup(self) -> None:
        """Called when the user opens the module the first time and the widget is initialized."""
        ScriptedLoadableModuleWidget.setup(self)

        # Load widget from .ui file (created by Qt Designer).
        # Additional widgets can be instantiated manually and added to self.layout.
        uiWidget = slicer.util.loadUI(self.resourcePath("UI/Telemetry.ui"))
        self.layout.addWidget(uiWidget)
        self.ui = slicer.util.childWidgetVariables(uiWidget)

        # Set scene in MRML widgets. Make sure that in Qt designer the top-level qMRMLWidget's
        # "mrmlSceneChanged(vtkMRMLScene*)" signal in is connected to each MRML widget's.
        # "setMRMLScene(vtkMRMLScene*)" slot.
        uiWidget.setMRMLScene(slicer.mrmlScene)

        # Create logic class. Logic implements all computations that should be possible to run
        # in batch mode, without a graphical user interface.
        self.logic = TelemetryLogic()

        # Connections
        
        self.extensionSelectionGroupBox = qt.QGroupBox("Select Extensions for Telemetry")
        self.extensionSelectionLayout = qt.QVBoxLayout()

        # Initialize extensionComboBoxes
        self.extensionComboBoxes = {}

        # Get the list of installed extensions
        self.extensions = slicer.app.extensionsManagerModel().installedExtensions

        # Get the selected extensions. Since `QSettings.value()` returns list as tuple,
        # convert back to list.
        settings = qt.QSettings()
        enabledExtensions = list(settings.value("enabledExtensions", []))
        disabledExtensions = list(settings.value("disabledExtensions", []))
        defaultExtensions = list(settings.value("defaultExtensions", []))

        for extension in self.extensions:
            layout = qt.QHBoxLayout()
            label = qt.QLabel(extension)
            comboBox = qt.QComboBox()
            comboBox.addItems(["default", "enable", "disable"])

            # Set the initial state based on saved settings
            if extension in enabledExtensions:
                comboBox.setCurrentIndex(1)
            elif extension in disabledExtensions:
                comboBox.setCurrentIndex(2)
            else:
                comboBox.setCurrentIndex(0)
                self.saveExtensionState(extension, 0)

            comboBox.currentIndexChanged.connect(lambda index, ext=extension: self.saveExtensionState(ext, index))
            layout.addWidget(label)
            layout.addWidget(comboBox)
            self.extensionSelectionLayout.addLayout(layout)
            self.extensionComboBoxes[extension] = comboBox

        self.extensionSelectionGroupBox.setLayout(self.extensionSelectionLayout)
        self.layout.addWidget(self.extensionSelectionGroupBox)

        self.comboBox = qt.QComboBox()
        self.comboBox.addItems(["default allowed", "default denied"])

        # Check if the setting exists
        if settings.contains("TelemetryDefaultPermission"):
            value = settings.value("TelemetryDefaultPermission", False)
            if isinstance(value, str):
                current_permission = value.lower() == 'true'
            else:
                current_permission = bool(value)
        else:
            current_permission = False  # Default to denied if the setting does not exist

        self.comboBox.setCurrentIndex(0 if current_permission else 1)
        self.comboBox.currentIndexChanged.connect(lambda index: settings.setValue("TelemetryDefaultPermission", index == 0))
        self.layout.addWidget(self.comboBox)

        # Buttons
        self.ui.applyButton.connect("clicked(bool)", self.onApplyButton)

    def saveExtensionState(self, extension, index):
        settings = qt.QSettings()
        enabledExtensions = list(settings.value("enabledExtensions", []))
        disabledExtensions = list(settings.value("disabledExtensions", []))
        defaultExtensions = list(settings.value("defaultExtensions", []))

        if index == 1:
            if extension not in enabledExtensions:
                enabledExtensions.append(extension)
            if extension in disabledExtensions:
                disabledExtensions.remove(extension)
            if extension in defaultExtensions:
                defaultExtensions.remove(extension)
        elif index == 2:
            if extension not in disabledExtensions:
                disabledExtensions.append(extension)
            if extension in enabledExtensions:
                enabledExtensions.remove(extension)
            if extension in defaultExtensions:
                defaultExtensions.remove(extension)
        else:
            if extension not in defaultExtensions:
                defaultExtensions.append(extension)
            if extension in enabledExtensions:
                enabledExtensions.remove(extension)
            if extension in disabledExtensions:
                disabledExtensions.remove(extension)

        settings.setValue("enabledExtensions", enabledExtensions)
        settings.setValue("disabledExtensions", disabledExtensions)
        settings.setValue("defaultExtensions", defaultExtensions)
    

    def cleanup(self) -> None:
        """Called when the application closes and the module widget is destroyed."""
        pass

    def enter(self) -> None:
        """Called each time the user opens this module."""
        pass

    def exit(self) -> None:
        """Called each time the user opens a different module."""
        pass

    def onApplyButton(self) -> None:
        """Run when user clicks "Apply" button."""
        try:
            self.logic.logAnEvent()
        except Exception as e:
            slicer.util.errorDisplay("Failed to send function count to server. See Python Console for error details.")
            import traceback
            traceback.print_exc()

                
#
# TelemetryLogic
#


class TelemetryLogic(ScriptedLoadableModuleLogic):

    def __init__(self) -> None:
        """Called when the logic class is instantiated. Can be used for initializing member variables."""
        ScriptedLoadableModuleLogic.__init__(self)

    @staticmethod
    def readLoggedEventsFromFile(csv_file_path):
        try:
            with open(csv_file_path, "r") as csvfile:
                reader = csv.DictReader(csvfile)
                return [row for row in reader]
        except Exception as e:
            print(f"Error loading events from CSV file: {e}")
            return []

    @staticmethod
    def saveLoggedEventsToFile(csv_file_path, logged_events):
        try:
            with open(csv_file_path, "w", newline='') as csvfile:
                fieldnames = ["component", "event", "day", "times"]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                for event in logged_events:
                    assert fieldnames == list(event.keys())
                    writer.writerow(event)
        except Exception as e:
            print(f"Error saving events to CSV file: {e}")

    @staticmethod
    def clearLoggedEventsFile(csv_file_path):
        with open(csv_file_path, 'w') as csvfile:
            csvfile.truncate()

    def logAnEvent(self):
        # Log this event
        if hasattr(slicer.app, 'logUsageEvent') and slicer.app.isUsageLoggingSupported:
            slicer.app.logUsageEvent("Telemetry", "logAnEvent")


#
# TelemetryTest
#


class TelemetryTest(ScriptedLoadableModuleTest):

    def setUp(self):
        """Do whatever is needed to reset the state - typically a scene clear will be enough."""
        slicer.mrmlScene.Clear()

    def runTest(self):
        """Run as few or as many tests as needed here."""
        self.setUp()
        self.test_Telemetry1()

    def test_Telemetry1(self):
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
        pass
