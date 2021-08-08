#!/usr/bin/python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
"""
Created on 19.12.2017

@author: mstoffel
"""

from c8yMQTT import C8yMQTT
from configparser import RawConfigParser
import logging
import sys
import os
from threading import Thread
import threading
import time
import io
import psutil
import socket
import json
import requests
from requests.auth import HTTPBasicAuth
from zipfile import ZipFile
from device_proxy import DeviceProxy
import concurrent.futures


def sendConfiguration():
    with open(config_file, "r") as configFile:
        configString = configFile.read()
    configString = '113,"' + configString + '"'
    c8y.logger.debug("Sending Config String:" + configString)
    c8y.publish("s/us", configString)


def getserial():
    # Extract serial from cpuinfo file can be set static by removing the try catch block
    cpuserial = "0000000000000000"
    if config.has_option("device", "serial"):
        cpuserial = config.get("device", "serial")
        return cpuserial
    try:
        f = open("/proc/cpuinfo", "r")
        for line in f:
            if line[0:6] == "Serial":
                cpuserial = line[10:26]
        f.close()
    except:
        cpuserial = "ERROR000000000"

    return cpuserial


def getrevision():
    # Extract board revision from cpuinfo file
    myrevision = "0000"
    try:
        f = open("/proc/cpuinfo", "r")
        for line in f:
            if line[0:8] == "Revision":
                length = len(line)
                myrevision = line[11 : length - 1]
        f.close()
    except:
        myrevision = "ERROR0000"
    c8y.logger.debug("Found HW Version: " + myrevision)
    return myrevision


def gethardware():
    # Extract board revision from cpuinfo file
    myrevision = "0000"
    try:
        f = open("/proc/cpuinfo", "r")
        for line in f:
            if line[0:8] == "Hardware":
                length = len(line)
                myrevision = line[11 : length - 1]
        f.close()
    except:
        myrevision = "ERROR0000"
    c8y.logger.debug("Found Hardware: " + myrevision)
    return myrevision


def serviceRestart(cause):
    c8y.logger.info("Service Restart due to: " + cause )
    os.system('sudo service c8y restart')
        

def reboot(cause):
    c8y.logger.info("Rebooting due to: " + cause )
    os.system('sudo reboot')


def sendCPULoad():
    tempString = "995,," + str(psutil.cpu_percent())
    c8y.logger.debug("Sending CPULoad: " + tempString)
    c8y.publish("s/uc/pi", tempString)


def sendMemory():
    tempString = (
        "996,,"
        + str(psutil.virtual_memory().total >> 20)
        + ","
        + str(psutil.virtual_memory().available >> 20)
        + ","
        + str(psutil.swap_memory().total >> 20)
    )
    c8y.logger.debug("Sending Memory: " + tempString)
    c8y.publish("s/uc/pi", tempString)


def sendMeasurements(stopEvent, interval):
    c8y.logger.info("Starting sendMeasurement with interval: " + str(interval))
    try:
        while True:
            c8y.logger.info("sendMeasurements called")

            try:
                sense.send()
            except Exception:
                c8y.logger.info("No sense hat found omitting.")

            sendCPULoad()
            sendMemory()
            if stopEvent.wait(timeout=interval):
                c8y.logger.info("sendMeasurement was stopped..")
                break
    except (KeyboardInterrupt, SystemExit):
        c8y.logger.info("Exiting sendMeasurement...")
        sys.exit()


def on_message_default(client, obj, msg):
    message = msg.payload.decode("utf-8")
    c8y.logger.info(
        "Message Received: " + msg.topic + " " + str(msg.qos) + " " + message
    )

    if message.startswith("71"):
        fields = message.split(",")
        c8y.token = fields[1]
        c8y.logger.info("New JWT Token received")
    if message.startswith("510"):
        Thread(target=restart).start()

    if message.startswith("513"):
        Thread(target=updateConfig, args=(message,)).start()

    if message.startswith("520"):
        c8y.logger.info("Received Config Upload. Sending config")
        sendConfiguration()

    if message.startswith("1001"):
        setCommandExecuting("c8y_Message")
        try:
            sense.displayMessage(message)
            setCommandSuccessfull("c8y_Message")
        except Exception as e:
            c8y.logger.error("Sense Hat Error: omitting.")
            setCommandFailed("c8y_Message", str(e))

    if message.startswith("1003"):
        fields = message.split(",")
        tcp_host = fields[2]
        tcp_port = int(fields[3])
        connection_key = fields[4]
        c8y.logger.info("Received Remote Connect.")
        setCommandExecuting("c8y_RemoteAccessConnect")
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(
                remoteConnect,
                tcp_host,
                tcp_port,
                connection_key,
                config.get("device", "host"),
            )
            return_value = future.result()
            c8y.logger.info("Remote Connect Result:" + return_value)
            if return_value.startswith("success"):
                setCommandSuccessfull("c8y_RemoteAccessConnect")
            else:
                setCommandFailed("c8y_RemoteAccessConnect", return_value)

    if message.startswith("516"):
        fields = message.split(",")
        if len(fields) == 5:
            name = fields[2]
            version = fields[3]
            url = fields[4]
            c8y.logger.info("Software Update:" + name + " Version: " + version)
            Thread(
                target=softwareUpdate,
                args=(
                    name,
                    version,
                    url,
                ),
            ).start()
        if len(fields) < 5:
            setCommandExecuting("c8y_SoftwareList")
            setCommandFailed("c8y_SoftwareList", "The agent cannot delete itself.")
        if len(fields) > 5:
            setCommandExecuting("c8y_SoftwareList")
            setCommandFailed(
                "c8y_SoftwareList",
                "Only one version of piAgent is supported at a time.",
            )


def remoteConnect(tcp_host, tcp_port, connection_key, base_url):
    try:
        c8y.logger.info(
            "Starting Remote to: "
            + str(tcp_host)
            + ":"
            + str(tcp_port)
            + " Key: "
            + str(connection_key)
            + " url: "
            + str(base_url)
        )
        if c8y.cert_auth:
            devProx = DeviceProxy(
                tcp_host,
                tcp_port,
                connection_key,
                base_url,
                None,
                None,
                None,
                c8y.token,
            )
        else:
            devProx = DeviceProxy(
                tcp_host,
                tcp_port,
                connection_key,
                base_url,
                c8y.tenant,
                c8y.user,
                c8y.password,
                None,
            )
        devProx.connect()
        c8y.logger.info("Remote Connection successfull finished")
        return "success"
    except Exception as e:
        c8y.logger.error("Remote Connection error:" + str(e))
        return str(e)


def createDir(filename):
    if not os.path.exists(os.path.dirname(filename)):
        try:
            os.makedirs(os.path.dirname(filename))
        except OSError as exc:  # Guard against race condition
            if exc.errno != errno.EEXIST:
                raise


def getRelease():
    try:
        with open("release") as f:
            release = f.read()
            c8y.logger.info("Release: " + str(release))
            return str(release)
    except Exception as e:
        c8y.logger.error("Error getting release file: " + str(e))
        return "error getting release file"


def softwareUpdate(name, version, url):
    try:
        if name.startswith("piAgent"):
            # Download new firmware
            if c8y.cert_auth:
                header = {"Authorization": "Bearer " + c8y.token}
                r = requests.get(url, headers=header)
            else:
                r = requests.get(
                    url, auth=HTTPBasicAuth(c8y.tenant + "/" + c8y.user, c8y.password)
                )
            setCommandExecuting("c8y_SoftwareList")
            c8y.logger.info("Download result: " + str(r.status_code))

            ## write downloaded new software file to disk
            newSoftwareFile = (
                "./software_download/release-" + name + "-" + version + ".zip"
            )
            createDir(newSoftwareFile)
            with open(newSoftwareFile, "wb") as f:
                f.write(r.content)

            # create backup of old version
            oldRelease = getRelease()

            c8y.logger.info("OldRelease: " + str(oldRelease))
            backupFile = "./backup/backup-" + oldRelease + ".zip"
            c8y.logger.info("BackupFile: " + str(backupFile))
            createDir(backupFile)
            exclude = (".", "backup", "c8yAgent.log", "zip")
            with ZipFile(backupFile, "w") as backup:
                files = [f for f in os.listdir(".") if os.path.isfile(f)]
                for file in files:
                    if not file.startswith(exclude) and not file.endswith(exclude):
                        c8y.logger.info("Adding to backup: " + str(file))
                        backup.write(file)

            # install new release
            with ZipFile(newSoftwareFile, "r") as newrelease:
                newrelease.extractall(".")

            # Write new version to release file
            with open("release", "wt") as releasefile:
                releasefile.write(version)
            c8y.publish("s/us", "116,piAgent," + getRelease() + ",")
            setCommandSuccessfull("c8y_SoftwareList")
            serviceRestart("New Software Installed.")
        else:
            c8y.logger.info("SoftwareUpdate ignoring unsupported Software " + str(e))
            setCommandFailed(
                "c8y_SoftwareList",
                "Only piAgent is supported as a software update. Feel free to implement other funtions.",
            )
    except Exception as e:
        c8y.logger.info("SoftwareUpdateError: " + str(e))
        setCommandFailed("c8y_SoftwareList", str(e))


def on_message_startup(client, obj, msg):
    # Can be used to process messages while startup
    message = msg.payload.decode("utf-8")
    c8y.logger.info(
        "On_Message_Startup Received: " + msg.topic + " " + str(msg.qos) + " " + message
    )


def setCommandExecuting(command):
    c8y.logger.info("Setting command: " + command + " to executing")
    c8y.publish("s/us", "501," + command)


def setCommandSuccessfull(command):
    c8y.logger.info("Setting command: " + command + " to successful")
    c8y.publish("s/us", "503," + command)


def setCommandFailed(command, errorMessage):
    c8y.logger.info("Setting command: " + command + " to failed cause: " + errorMessage)
    c8y.publish("s/us", "502," + command + "," + errorMessage)


def restart():
    if config.get("device", "reboot") != "1":
        c8y.logger.info("Rebooting")
        c8y.publish("s/us", "501,c8y_Restart")
        config.set("device", "reboot", "1")
        with open(config_file, "w") as configfile:
            config.write(configfile)
        c8y.disconnect()
        reboot("Received restart command from platform.")
    else:
        c8y.logger.info("Received restart but already in progress")


def updateConfig(message):
    c8y.logger.info("UpdateConfig")
    if config.get("device", "config_update") != "1":
        plain_message = c8y.getPayload(message).strip('"')
        with open(config_file, "w") as configFile:
            config.readfp(io.StringIO(plain_message))
            c8y.logger.info("Current config:" + str(config.sections()))
            config.set("device", "config_update", "1")
            config.write(configFile)
        c8y.logger.info("Sending Config Update executing")
        setCommandExecuting("c8y_Configuration")
        setCommandSuccessfull("c8y_Configuration")
        serviceRestart("ConfigUpdate")

    else:
        c8y.logger.info("Received Config Update but already in progress")


def runAgent():
    # Enter Device specific values
    stopEvent.clear()
    if c8y.initialized == False:
        c8y.bootstrap(config.get("device", "bootstrap_pwd"))
    if c8y.initialized == False:
        c8y.logger.info("Could not register. Exiting.")
        exit()
    ## Connect Agent Startup
    connected = c8y.connect(on_message_startup, config.get("device", "subscribe"))
    c8y.logger.info("Connection Result:" + str(connected))
    if connected == 5 and not config.getboolean("device", "cert_auth"):
        c8y.reset()
        serviceRestart("Invalid credentials. Resetting!!!")
        exit()
    if connected != 0:
        serviceRestart("Connection Error: " + str(connected) + " restarting.")
        exit()
    c8y.initDevice(
        config.get("device", "name") + "-" + c8y.clientId,
        config.get("device", "devicetype"),
        c8y.clientId,
        gethardware(),
        getrevision(),
        config.get("device", "operations"),
        config.get("device", "requiredinterval"),
    )
    ### Get Pending Operations
    c8y.publish("s/us", "114," + config.get("device", "operations"))

    ### Clean up old Software List operations
    for _ in range(5):
        setCommandExecuting("c8y_SoftwareList")
    for _ in range(25):
        setCommandFailed("c8y_SoftwareList", "Cleanup")

    ### Check if reboot flag is set
    if config.get("device", "reboot") == "1":
        c8y.logger.info("reboot is active. Publishing Acknowledgement..")
        setCommandSuccessfull("c8y_Restart")
        config.set("device", "reboot", "0")
        with open(config_file, "w") as configfile:
            config.write(configfile)

    ### Check if config
    if config.get("device", "config_update") == "1":
        c8y.logger.info("Config Update is active. Publishing Acknowledgement..")
        setCommandSuccessfull("c8y_Configuration")
        config.set("device", "config_update", "0")
        with open(config_file, "w") as configfile:
            config.write(configfile)

    ### Create SmartRest Templat must be deleted in UI if new version form here should be deployd
    c8y.createSmartRestTemplates()
    c8y.publish("s/us", "114," + config.get("device", "operations"))
    c8y.publish("s/us", "116,piAgent," + getRelease() + ",")

    sendConfiguration()
    time.sleep(2)
    c8y.disconnect()
    time.sleep(1)

    ### Operational connection
    c8y.connect(on_message_default, config.get("device", "subscribe"))
    c8y.logger.info("Starting sendMeasurements.")
    sendThread = Thread(
        target=sendMeasurements,
        args=(stopEvent, int(config.get("device", "sendinterval"))),
    )
    sendThread.start()


stopEvent = threading.Event()

### Reading Config file
config_file = "pi.properties"
config = RawConfigParser()
config.read(config_file)


### Initialize MQTT Module
c8y = C8yMQTT(
    config.get("device", "identifier"),
    config.get("device", "host"),
    int(config.get("device", "port")),
    config.getboolean("device", "tls"),
    config.get("device", "cacert"),
    config.getboolean("device", "cert_auth"),
    config.get("device", "client_cert"),
    config.get("device", "client_key"),
    loglevel=logging.getLevelName(config.get("device", "loglevel")),
)

try:
    ### Try to load sensehat extension
    from sensehat import Sense

    sense = Sense(c8y, serviceRestart)
except Exception as e:
    c8y.logger.error("Sense Hat Error:" + str(e))
try:
    runAgent()
except Exception as e:
    c8y.logger.error("runAgent Error:" + str(e))
