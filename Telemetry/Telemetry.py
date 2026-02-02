from datetime import datetime
import csv
import json
import os
import traceback
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
    # Do not collect statistics in testing mode
    if slicer.app.testingEnabled():
        return
    if not TelemetryLogic.shouldLogUsageEvent(component):
        print(f"Component {component} is not in the enabled or default extensions with permission. Event not logged.")
        return

    TelemetryLogic.logUsageEvent(component, event)

# Connect to the usageEventLogged signal if usage logging is supported
if hasattr(slicer.app, 'usageEventLogged') and slicer.app.isUsageLoggingSupported:
    slicer.app.usageEventLogged.connect(onUsageEventLogged)


class Telemetry(ScriptedLoadableModule):
    """Uses ScriptedLoadableModule base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = _("Telemetry")
        self.parent.categories = [translate("qSlicerAbstractCoreModule", "Utilities")]
        self.parent.dependencies = []
        self.parent.contributors = ["Dominguez Bernardo", "Andras Lasso (PerkLab, Queen's University)"]
        # _() function marks text as translatable to other languages
        self.parent.helpText = _("""
This extension allows 3D Slicer extensions to gather information on what software features are used. This information helps demonstrating impact, which is essential for getting continuous funding for maintenance and improvements.
See more information at <a href='https://github.com/Slicer/SlicerTelemetry'>Telemetry extension website</a>.
""")
        self.parent.acknowledgementText = _("""
Bernardo Dominguez developed this module for his professional supervised practices of engineering studies at UTN-FRRO under the supervision and advice of PhD. Andras Lasso at Perklab and guidance from Slicer core developers""")
        # Initialize the telemetry logic on startup
        slicer.app.connect("startupCompleted()", self.onStartupCompleted)

    def onStartupCompleted(self):
        """Initialize telemetry functionality after Slicer startup is complete."""
        # Do not initialize telemetry in testing mode
        if slicer.app.testingEnabled():
            return

        # Create logic instance to handle telemetry functionality
        logic = TelemetryLogic()

            # Log startup event for basic statistics
        if hasattr(slicer.app, 'logUsageEvent') and slicer.app.isUsageLoggingSupported:
            slicer.app.logUsageEvent("Telemetry", "SlicerStartup")

        # Set up timer for showing permission popup
        qt.QTimer.singleShot(4000, lambda: self.showInitialTelemetrySetup())

        # Set up timer to check if telemetry data should be sent on startup
        def check_and_send_telemetry():
            logic = TelemetryLogic()
            if logic.shouldPromptForTelemetryUpload():
                logic.usageUpload()
        qt.QTimer.singleShot(5000, check_and_send_telemetry)

        # Connect extension installation handler
        slicer.app.extensionsManagerModel().extensionInstalled.connect(logic.onExtensionInstalled)

    def showInitialTelemetrySetup(self):
        """Show the initial telemetry permission setup if needed."""
        # Do not show popups in testing mode
        if slicer.app.testingEnabled():
            return
        try:
            settings = qt.QSettings()
            if settings.value("TelemetryDefaultPermission", None) is None:
                dialog = TelemetryPermissionDialog()
                dialog.exec_()
        except Exception as e:
            print(f"Error showing initial telemetry setup: {e}")
            traceback.print_exc()
            # Fallback to a simple message box
            slicer.util.infoDisplay("Error loading telemetry setup dialog. Please check the console for details.")

#
# TelemetryWidget
#


class TelemetrySendDialog(qt.QDialog):
    """
    Dialog for sending telemetry data with user options.
    """
    def __init__(self, parent=None, detailsText=""):
        super().__init__(parent)
        self.setWindowTitle("Send Telemetry Data")
        uiWidget = slicer.util.loadUI(self.resourcePath("UI/TelemetrySendDialog.ui"))
        # Properly add the loaded UI to the dialog
        layout = qt.QVBoxLayout()
        layout.addWidget(uiWidget)
        self.setLayout(layout)
        self.ui = slicer.util.childWidgetVariables(uiWidget)
        if hasattr(self.ui, 'detailsTextEdit'):
            self.ui.detailsTextEdit.setPlainText(detailsText)
        else:
            print("Warning: detailsTextEdit not found in TelemetrySendDialog UI.")
        if hasattr(self.ui, 'buttonBox'):
            self.ui.buttonBox.accepted.connect(self.accept)
            self.ui.buttonBox.rejected.connect(self.reject)
        else:
            print("Warning: buttonBox not found in TelemetrySendDialog UI.")

        # Add a button to show statistics dashboard
        self.statsButton = qt.QPushButton("Show Statistics Dashboard")
        layout.addWidget(self.statsButton)
        self.statsButton.clicked.connect(self.onShowStatsDashboard)

    def onShowStatsDashboard(self):
        TelemetryWidget.showStatsDashboard()

    def getUserChoice(self):
        if self.ui.sendOnceRadio.isChecked():
            return "send-once"
        elif self.ui.dontSendOnceRadio.isChecked():
            return "dont-send-once"
        elif self.ui.alwaysSendRadio.isChecked():
            return "always"
        elif self.ui.neverSendRadio.isChecked():
            return "never"
        return None


    def resourcePath(self, filename):
        moduleDir = os.path.dirname(os.path.realpath(__file__))
        return os.path.join(moduleDir, 'Resources', filename)


class TelemetryWidget(ScriptedLoadableModuleWidget):
    """
    Telemetry module widget.

    """

    # Class variable to store the stats dashboard widget
    _stats_web_widget = None


    def __init__(self, parent=None) -> None:
        """Called when the user opens the module the first time and the widget is initialized."""
        ScriptedLoadableModuleWidget.__init__(self, parent)
        self.logic = None

    def setup(self) -> None:
        """Called when the user opens the module the first time and the widget is initialized."""
        ScriptedLoadableModuleWidget.setup(self)

        try:
            # Load the new summary UI
            uiWidget = slicer.util.loadUI(self.resourcePath("UI/TelemetrySummary.ui"))
            self.layout.addWidget(uiWidget)
            self.ui = slicer.util.childWidgetVariables(uiWidget)
            self.logic = TelemetryLogic()

            # Connect the new buttons
            self.ui.configureButton.connect("clicked(bool)", self.showPermissionDialog)
            self.ui.logEventButton.connect("clicked(bool)", self.onApplyButton)
            self.ui.showStatsButton.connect("clicked(bool)", self.showStatsDashboard)
            if hasattr(self.ui, 'sendDataButton'):
                self.ui.sendDataButton.connect("clicked(bool)", self.showSendTelemetryDialog)

            # Initialize and persist URL field in summary UI
            try:
                if hasattr(self.ui, 'urlTextEdit'):
                    settings = qt.QSettings()
                    stored_url = settings.value("TelemetrySendUrl", None)
                    current_url = stored_url or TelemetryLogic().url
                    self.ui.urlTextEdit.setPlainText(current_url)
                    # Persist changes when user edits the field
                    def on_url_changed():
                        new_url = self.ui.urlTextEdit.toPlainText().strip()
                        if new_url:
                            settings.setValue("TelemetrySendUrl", new_url)
                    # QTextEdit doesn't have textChanged for plainText; use textChanged signal
                    if hasattr(self.ui.urlTextEdit, 'textChanged'):
                        self.ui.urlTextEdit.textChanged.connect(on_url_changed)
            except Exception as e:
                print(f"Warning: failed to initialize URL field in summary UI: {e}")

            # Update the status display
            self.updateStatusDisplay()
            # Check if initial configuration is needed
            self.checkInitialConfiguration()
            # Also update status after a short delay to ensure settings are loaded
            qt.QTimer.singleShot(1000, self.updateStatusDisplay)
        except Exception as e:
            print(f"Error loading new UI: {e}")
            slicer.util.infoDisplay("Error loading Telemetry UI. Please check the console for details.")

    def showSendTelemetryDialog(self):
        """Show the TelemetrySendDialog for sending telemetry data (always sends if accepted)."""
        try:
            # Ensure latest URL from summary UI is persisted before sending
            try:
                if hasattr(self.ui, 'urlTextEdit'):
                    settings = qt.QSettings()
                    new_url = self.ui.urlTextEdit.toPlainText().strip()
                    if new_url:
                        settings.setValue("TelemetrySendUrl", new_url)
            except Exception:
                pass
            TelemetryWidget.handleTelemetryUpload(force=True)
        except Exception as e:
            print(f"Error showing TelemetrySendDialog: {e}")
            traceback.print_exc()
            slicer.util.infoDisplay("Error loading Telemetry Send Dialog. Please check the console for details.")

    def showPermissionDialog(self):
        """Show the comprehensive telemetry permission dialog."""
        try:
            dialog = TelemetryPermissionDialog()
            if dialog.exec_() == qt.QDialog.Accepted:
                self.updateStatusDisplay()
        except Exception as e:
            print(f"Error showing permission dialog: {e}")
            traceback.print_exc()
            # Fall back to a simple message box
            slicer.util.infoDisplay("Error loading permission dialog. Please check the console for details.")

    def showStatsDashboard(self):
        """Show the statistics dashboard."""
        TelemetryWidget.showStatsDashboard()

    def updateStatusDisplay(self):
        """Update the status label based on current settings."""
        try:
            settings = qt.QSettings()
            telemetryResponse = settings.value("TelemetryUserResponse", None)
            defaultPermission = settings.value("TelemetryDefaultPermission", None)
            if isinstance(defaultPermission, str):
                defaultPermission = defaultPermission.lower() == 'true'
            enabledExtensions = settings.value("enabledExtensions", []) or []
            disabledExtensions = settings.value("disabledExtensions", []) or []
            # Convert tuples to lists if necessary
            if isinstance(enabledExtensions, tuple):
                enabledExtensions = list(enabledExtensions)
            if isinstance(disabledExtensions, tuple):
                disabledExtensions = list(disabledExtensions)
            # Generate status message
            if telemetryResponse == "no":
                status = "🔒 Telemetry is completely disabled"
                styleSheet = "font-weight: bold;"
            elif defaultPermission is True:
                if disabledExtensions:
                    status = f"✅ Telemetry enabled by default, disabled for {len(disabledExtensions)} extension(s)"
                else:
                    status = "✅ Telemetry enabled by default for all extensions"
                styleSheet = "font-weight: bold;"
            elif defaultPermission is False:
                if enabledExtensions:
                    status = f"⚠️ Telemetry disabled by default, enabled for {len(enabledExtensions)} extension(s)"
                else:
                    status = "⚠️ Telemetry disabled by default for all extensions"
                styleSheet = "font-weight: bold;"
            else:
                status = "❓ Telemetry preferences not configured"
                styleSheet = "font-weight: bold;"


            # Check if statusLabel exists
            if hasattr(self.ui, 'statusLabel') and self.ui.statusLabel:
                self.ui.statusLabel.setStyleSheet(styleSheet)
                self.ui.statusLabel.setText(status)
            else:
                print("Warning: statusLabel not found in UI")
        except Exception as e:
            print(f"Error updating status display: {e}")
            traceback.print_exc()

    def checkInitialConfiguration(self):
        """Check if telemetry needs initial configuration."""
        # Do not show configuration dialog in testing mode
        if slicer.app.testingEnabled():
            return True

        settings = qt.QSettings()
        defaultPermission = settings.value("TelemetryDefaultPermission", None)
        telemetryResponse = settings.value("TelemetryUserResponse", None)



        if defaultPermission is None and telemetryResponse is None:
            # Show configuration dialog automatically
            qt.QTimer.singleShot(2000, self.showPermissionDialog)

        return defaultPermission is not None or telemetryResponse is not None

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
            traceback.print_exc()

    @staticmethod
    def showTelemetryPermissionPopup():
        """Show comprehensive telemetry permission dialog."""
        # Do not show popups in testing mode
        if slicer.app.testingEnabled():
            return

        try:
            settings = qt.QSettings()
            if settings.value("TelemetryDefaultPermission", None) is None:
                dialog = TelemetryPermissionDialog()
                dialog.exec_()
        except Exception as e:
            print(f"Error in showTelemetryPermissionPopup: {e}")
            traceback.print_exc()

    @staticmethod
    def handleTelemetryUpload(force=False):
        """Handle telemetry upload, optionally forcing immediate send/prompt."""
        logic = TelemetryLogic()
        logic.usageUpload(force=force)


    @staticmethod
    def handleTelemetryDialogResponse(response, checkbox, dialog):
        """Handle user response from telemetry dialog."""
        do_not_ask_again = checkbox.isChecked()
        if do_not_ask_again:
            settings = qt.QSettings()
            if response == qt.QMessageBox.Yes:
                settings.setValue("TelemetryUserResponse", "yes")
            elif response == qt.QMessageBox.No:
                settings.setValue("TelemetryUserResponse", "no")
            elif response == qt.QMessageBox.Cancel:
                settings.setValue("TelemetryUserResponse", "cancel")
        if response == qt.QMessageBox.Yes:
            print("User accepted the telemetry upload.")
            logic = TelemetryLogic()
            logic.usageUpload()
        elif response == qt.QMessageBox.No:
            print("User rejected the telemetry upload.")
        elif response == qt.QMessageBox.Cancel:
            print("User chose to be asked later.")

        dialog.close()

    @staticmethod
    def showStatsDashboard():
        """Show statistics dashboard in a web widget."""
        # Create or reuse the web widget
        if TelemetryWidget._stats_web_widget is None:
            TelemetryWidget._stats_web_widget = qSlicerWebWidget()

        webWidget = TelemetryWidget._stats_web_widget
        loggedEvents = TelemetryLogic.readLoggedEventsFromFile('telemetry_events.csv')

        events_json = json.dumps(loggedEvents)
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
                    display: flex;
                    flex-direction: row;
                    justify-content: space-between;
                    gap: 20px;
                }}
                .chart-col {{
                    flex: 1 1 0;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
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
                <div class="chart-col">
                    <div class="chart-title">Event Timeline</div>
                    <div id="time-chart"></div>
                </div>
                <div class="chart-col">
                    <div class="chart-title">Module Usage</div>
                    <div id="module-chart"></div>
                </div>
                <div class="chart-col">
                    <div class="chart-title">Event Types</div>
                    <div id="event-chart"></div>
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
                        .width(350)
                        .height(250)
                        .margins({{top: 20, right: 20, bottom: 40, left: 40}})
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
                        .width(350)
                        .height(250)
                        .margins({{top: 20, right: 20, bottom: 80, left: 40}})
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
                        .width(350)
                        .height(250)
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

        webWidget.setHtml(htmlContent)
        webWidget.show()
        webWidget.setMinimumWidth(1100)
        webWidget.setMinimumHeight(400)
        webWidget.setWindowTitle("Slicer Usage Statistics")


#
# TelemetryPermissionDialog
#

class TelemetryPermissionDialog(qt.QDialog):
    """
    A comprehensive dialog for telemetry permissions with collapsible extension settings.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        try:
            # Load the UI file
            uiWidget = slicer.util.loadUI(self.resourcePath("UI/TelemetryPermissionDialog.ui"))
            self.ui = slicer.util.childWidgetVariables(uiWidget)

            self.setWindowTitle("Anonymous Usage Statistics")
            self.setModal(True)

            mainLayout = qt.QVBoxLayout()
            mainLayout.addWidget(uiWidget)
            self.setLayout(mainLayout)

            # Set the initial size to match the UI file geometry
            self.resize(uiWidget.size)

            # Get UI elements
            self.allowByDefaultRadio = self.ui.allowByDefaultRadio
            self.disableByDefaultRadio = self.ui.disableByDefaultRadio
            self.noDataCollectionRadio = self.ui.noDataCollectionRadio
            self.toggleExtensionsButton = self.ui.toggleExtensionsButton
            self.extensionsScrollArea = self.ui.extensionsScrollArea
            self.extensionSettingsWidget = self.ui.extensionSettingsWidget
            self.scrollAreaWidgetContents = self.ui.scrollAreaWidgetContents

            self.extensionListLayout = self.scrollAreaWidgetContents.layout()

            self.buttonBox = self.ui.buttonBox

            # Configure scroll area for proper scrolling

            self.extensionsScrollArea.setVerticalScrollBarPolicy(qt.Qt.ScrollBarAsNeeded)

            self.extensionsScrollArea.setSizePolicy(qt.QSizePolicy.Expanding, qt.QSizePolicy.Expanding)

            # Initially hide the extensions area
            self.extensionsScrollArea.hide()
            self.extensionsExpanded = False

            # Store extension combo boxes for later reference
            self.extensionComboBoxes = {}

            # Connect signals
            self.toggleExtensionsButton.clicked.connect(self.toggleExtensionsDisplay)
            self.buttonBox.accepted.connect(self.onAccepted)
            self.buttonBox.rejected.connect(self.onRejected)

            # Connect radio buttons to update extension controls
            self.allowByDefaultRadio.toggled.connect(self.updateExtensionControls)
            self.disableByDefaultRadio.toggled.connect(self.updateExtensionControls)
            self.noDataCollectionRadio.toggled.connect(self.updateExtensionControls)

            # Load current settings and populate extension list
            self.loadCurrentSettings()
            self.populateExtensionList()
        except Exception as e:
            print(f"Error loading TelemetryPermissionDialog: {e}")
            traceback.print_exc()

    def resourcePath(self, filename):
        """Get resource file path."""
        moduleDir = os.path.dirname(os.path.realpath(__file__))
        return os.path.join(moduleDir, 'Resources', filename)

    def loadCurrentSettings(self):
        """Load current telemetry settings from QSettings."""
        settings = qt.QSettings()

        # Check default permission setting
        if settings.contains("TelemetryDefaultPermission"):
            defaultPermission = settings.value("TelemetryDefaultPermission", False)
            if isinstance(defaultPermission, str):
                defaultPermission = defaultPermission.lower() == 'true'
            else:
                defaultPermission = bool(defaultPermission)
        else:
            defaultPermission = None

        # Check if telemetry is completely disabled
        telemetryResponse = settings.value("TelemetryUserResponse", None)

        if telemetryResponse == "no" or telemetryResponse == "cancel":
            self.noDataCollectionRadio.setChecked(True)
        elif defaultPermission is True:
            self.allowByDefaultRadio.setChecked(True)
        elif defaultPermission is False:
            self.disableByDefaultRadio.setChecked(True)
        else:
            # Default to allow by default for first-time users
            self.allowByDefaultRadio.setChecked(True)
    def populateExtensionList(self):
        """Populate the extension list with current permissions."""
        # Get installed extensions
        extensions = slicer.app.extensionsManagerModel().installedExtensions
        # Get current extension settings
        settings = qt.QSettings()
        enabledExtensions = settings.value("enabledExtensions", [])
        disabledExtensions = settings.value("disabledExtensions", [])
        defaultExtensions = settings.value("defaultExtensions", [])
        # Convert tuples to lists if necessary
        if isinstance(enabledExtensions, tuple):
            enabledExtensions = list(enabledExtensions)
        if isinstance(disabledExtensions, tuple):
            disabledExtensions = list(disabledExtensions)
        if isinstance(defaultExtensions, tuple):
            defaultExtensions = list(defaultExtensions)
        # Ensure they are lists
        if enabledExtensions is None:
            enabledExtensions = []
        if disabledExtensions is None:
            disabledExtensions = []
        if defaultExtensions is None:
            defaultExtensions = []
        # Check if we have a valid layout
        if self.extensionListLayout is None:
            print("Warning: extensionListLayout not available, skipping extension list update")
            return
        # Clear existing layout
        while self.extensionListLayout.count():
            child = self.extensionListLayout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        # Add extension controls
        for extension in extensions:
            # Create horizontal layout for each extension
            extensionLayout = qt.QHBoxLayout()
            # Extension name label
            nameLabel = qt.QLabel(extension)
            nameLabel.setMinimumWidth(150)
            extensionLayout.addWidget(nameLabel)

            # Permission combo box
            permissionCombo = qt.QComboBox()
            permissionCombo.addItems(["Default", "Always Enable", "Always Disable"])

            # Set current state
            if extension in enabledExtensions:
                permissionCombo.setCurrentIndex(1)  # Always Enable
            elif extension in disabledExtensions:
                permissionCombo.setCurrentIndex(2)  # Always Disable
            else:
                permissionCombo.setCurrentIndex(0)  # Default
            # Store reference and connect signal
            self.extensionComboBoxes[extension] = permissionCombo
            permissionCombo.currentIndexChanged.connect(
                lambda index, ext=extension: self.onExtensionPermissionChanged(ext, index)
            )

            extensionLayout.addWidget(permissionCombo)
            extensionLayout.addStretch()

            # Add to main layout
            self.extensionListLayout.addLayout(extensionLayout)

        if not extensions:
            noExtensionsLabel = qt.QLabel("No extensions currently installed.")
            noExtensionsLabel.setStyleSheet("color: gray; font-style: italic;")
            self.extensionListLayout.addWidget(noExtensionsLabel)

        # Add stretch at the end to ensure proper scrolling behavior
        self.extensionListLayout.addStretch()

        # Ensure the scroll area content has proper size hints
        self.scrollAreaWidgetContents.setMinimumWidth(400)

    def onExtensionPermissionChanged(self, extension, index):
        """Handle changes to individual extension permissions."""
        # This will be saved when the dialog is accepted
        pass

    def updateExtensionControls(self):
        """Enable/disable extension controls based on main telemetry setting."""
        enabled = not self.noDataCollectionRadio.isChecked()

        # Enable/disable all extension combo boxes
        for comboBox in self.extensionComboBoxes.values():
            comboBox.setEnabled(enabled)

    def toggleExtensionsDisplay(self):
        """Toggle the visibility of the extensions area."""
        if self.extensionsExpanded:
            self.extensionsScrollArea.hide()
            self.toggleExtensionsButton.setText("▼ Show Extension Settings")
            self.extensionsExpanded = False
        else:
            self.extensionsScrollArea.show()
            self.toggleExtensionsButton.setText("▲ Hide Extension Settings")
            self.extensionsExpanded = True

    def onAccepted(self):
        """Save settings when dialog is accepted."""
        self.saveSettings()
        self.accept()

    def onRejected(self):
        """Handle dialog cancellation."""
        self.reject()

    def saveSettings(self):
        """Save all telemetry settings to QSettings."""
        settings = qt.QSettings()

        # Save main telemetry preference
        if self.allowByDefaultRadio.isChecked():
            settings.setValue("TelemetryDefaultPermission", True)
            settings.setValue("TelemetryUserResponse", "yes")
        elif self.disableByDefaultRadio.isChecked():
            settings.setValue("TelemetryDefaultPermission", False)
            settings.setValue("TelemetryUserResponse", "yes")
        else:  # No data collection
            settings.setValue("TelemetryDefaultPermission", False)
            settings.setValue("TelemetryUserResponse", "no")
        # Save individual extension settings
        enabledExtensions = []
        disabledExtensions = []
        defaultExtensions = []
        for extension, comboBox in self.extensionComboBoxes.items():
            index = comboBox.currentIndex
            if index == 1:  # Always Enable
                enabledExtensions.append(extension)
            elif index == 2:  # Always Disable
                disabledExtensions.append(extension)
            else:  # Default
                defaultExtensions.append(extension)

        settings.setValue("enabledExtensions", enabledExtensions)
        settings.setValue("disabledExtensions", disabledExtensions)
        settings.setValue("defaultExtensions", defaultExtensions)



#
# TelemetryLogic
#


class TelemetryLogic(ScriptedLoadableModuleLogic):

    def __init__(self) -> None:
        """Called when the logic class is instantiated. Can be used for initializing member variables."""
        ScriptedLoadableModuleLogic.__init__(self)
        # Initialize network manager for Qt-based uploads
        try:
            self.networkAccessManager = qt.QNetworkAccessManager()
            self.networkAccessManager.finished.connect(self.handleNetworkReply)
            self.http2Allowed = True
            self._haveQT = True
        except ModuleNotFoundError:
            self._haveQT = False
        # Initialize default/persisted destination URL
        try:
            settings = qt.QSettings()
            stored_url = settings.value("TelemetrySendUrl", None)
        except Exception:
            stored_url = None
        self.url = stored_url or "https://ber-dom.sao.dom.my.id/telemetry"
        self.headers = {"Content-Type": "application/json"}
        self.csv_file_path = 'telemetry_events.csv'
        self.urlsByReply = {}

    # Number of days to wait between telemetry uploads
    TELEMETRY_SEND_INTERVAL_DAYS = 7

    def shouldPromptForTelemetryUpload(self, interval_days=None):
        """Return True if enough days have passed since last telemetry upload, or if never sent."""
        if interval_days is None:
            interval_days = self.TELEMETRY_SEND_INTERVAL_DAYS
        settings = qt.QSettings()
        sendPolicy = settings.value("TelemetrySendPolicy", "ask")
        if sendPolicy == "always" or sendPolicy == "never":
            return False
        lastSent = settings.value("lastSent")
        if lastSent:
            try:
                lastSentDate = datetime.fromisoformat(lastSent)
                days_since = (datetime.now() - lastSentDate).days
                if days_since >= interval_days:
                    return True
                else:
                    return False
            except Exception as e:
                print(f"Error parsing lastSent date: {e}")
                return True
        # If never sent, show popup
        return True

    def usageUpload(self, force=False):
        """Upload usage data to server, showing popup if needed. If force=True, always send/prompt."""
        # Do not upload or show popups in testing mode
        if slicer.app.testingEnabled():
            return

        settings = qt.QSettings()
        sendPolicy = settings.value("TelemetrySendPolicy", "ask")
        if sendPolicy == "never" and not force:
            print("Telemetry send policy is 'never'. Not sending data.")
            return
        if sendPolicy == "always" and not force:
            self._sendTelemetryData()
            return
        # If not forced, check if enough time has passed
        if not force:
            if not self.shouldPromptForTelemetryUpload():
                print("Telemetry upload interval not reached. Not sending data.")
                return
        # Otherwise, show popup
        loggedEvents = TelemetryLogic.readLoggedEventsFromFile(self.csv_file_path)
        details = json.dumps(loggedEvents, indent=2)
        dialog = TelemetrySendDialog(detailsText=details)
        if dialog.exec_() == qt.QDialog.Accepted:
            choice = dialog.getUserChoice()
            if choice == "send-once":
                self._sendTelemetryData()
            elif choice == "always":
                settings.setValue("TelemetrySendPolicy", "always")
                self._sendTelemetryData()
            elif choice == "never":
                settings.setValue("TelemetrySendPolicy", "never")
                print("User chose never to send telemetry.")
            elif choice == "dont-send-once":
                print("User chose not to send telemetry this time.")
        else:
            print("Telemetry send dialog canceled.")

    def _sendTelemetryData(self, url=None):
        """Actually send the telemetry data and update lastSent."""
        settings = qt.QSettings()
        loggedEvents = TelemetryLogic.readLoggedEventsFromFile(self.csv_file_path)
        data_to_send = loggedEvents
        if not data_to_send:
            print("No logged events to send")
            return
        # Choose destination URL
        send_url = (url or self.url)
        try:
            if hasattr(self, '_haveQT') and self._haveQT:
                request = qt.QNetworkRequest(qt.QUrl(send_url))
                request.setHeader(qt.QNetworkRequest.ContentTypeHeader, "application/json")
                json_data = json.dumps(data_to_send)
                response = self.networkAccessManager.post(request, json_data.encode('utf-8'))
                self.urlsByReply[response] = send_url
            else:
                response = requests.post(send_url, headers=self.headers, json=loggedEvents)
                if response.status_code == 200:
                    print("Logged events sent to server")
                    TelemetryLogic.clearLoggedEventsFile(self.csv_file_path)
                    settings.setValue("lastSent", datetime.now().isoformat())
                else:
                    print(f"Error sending logged events to server: {response.status_code}")
                    print(f"Response content: {response.text}")
        except Exception as e:
            print(f"Error sending logged events to server: {e}")

    def handleNetworkReply(self, reply):
        """Handle Qt network reply (renamed from handleQtReply)."""
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

    @staticmethod
    def _createEmptyCSVFile(csv_file_path):
        """Create an empty CSV file with proper headers."""
        try:
            with open(csv_file_path, "w", newline='') as csvfile:
                fieldnames = ["component", "event", "day", "times"]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
            print(f"Created empty CSV file: {csv_file_path}")
        except Exception as e:
            print(f"Error creating empty CSV file: {e}")

    @staticmethod
    def readLoggedEventsFromFile(csv_file_path):
        try:
            # Check if file exists, if not create an empty one
            if not os.path.exists(csv_file_path):
                print(f"CSV file {csv_file_path} does not exist. Creating a new one.")
                TelemetryLogic._createEmptyCSVFile(csv_file_path)
                return []
            with open(csv_file_path, "r") as csvfile:
                reader = csv.DictReader(csvfile)
                return [row for row in reader]
        except Exception as e:
            print(f"Error loading events from CSV file: {e}")
            return []

    @staticmethod
    def saveLoggedEventsToFile(csv_file_path, logged_events):
        try:
            # Ensure the directory exists
            directory = os.path.dirname(csv_file_path)
            if directory and not os.path.exists(directory):
                os.makedirs(directory)
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

    @staticmethod
    def shouldLogUsageEvent(component):
        # Load settings
        settings = qt.QSettings()
        enabledExtensions = settings.value("enabledExtensions")
        disabledExtensions = settings.value("disabledExtensions")
        defaultExtensions = settings.value("defaultExtensions")
        telemetryDefaultPermission = settings.value("TelemetryDefaultPermission")

        if isinstance(enabledExtensions, tuple):
            enabledExtensions = list(enabledExtensions)
        if isinstance(disabledExtensions, tuple):
            disabledExtensions = list(disabledExtensions)
        if isinstance(defaultExtensions, tuple):
            defaultExtensions = list(defaultExtensions)

        # Ensure the settings are lists
        if enabledExtensions is None:
            enabledExtensions = []
        if disabledExtensions is None:
            disabledExtensions = []
        if defaultExtensions is None:
            defaultExtensions = []

        should_log = False

        # Check if the component should be logged
        if component in enabledExtensions:
            should_log = True
        elif component in disabledExtensions:
            should_log = False
        elif component in defaultExtensions:
            should_log = telemetryDefaultPermission
        else:
            should_log = False

        return should_log

    @staticmethod
    def logUsageEvent(component, event):
        # Do not log events in testing mode
        if slicer.app.testingEnabled():
            return
        if not TelemetryLogic.shouldLogUsageEvent(component):
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

    def logAnEvent(self):
        # Do not log events in testing mode
        if slicer.app.testingEnabled():
            return
        # Log this event
        if hasattr(slicer.app, 'logUsageEvent') and slicer.app.isUsageLoggingSupported:
            slicer.app.logUsageEvent("Telemetry", "logAnEvent")

    def onExtensionInstalled(self, extensionName):
        """Handle extension installation by updating default settings."""
        # Do not modify settings in testing mode
        if slicer.app.testingEnabled():
            return
        settings = qt.QSettings()
        telemetryDefaultPermission = settings.value("TelemetryDefaultPermission")
        print(f"Telemetry default permission: {telemetryDefaultPermission}")
        defaultExtensions = list(settings.value("defaultExtensions", []))
        if extensionName not in defaultExtensions:
            defaultExtensions.append(extensionName)
            settings.setValue("defaultExtensions", defaultExtensions)

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
