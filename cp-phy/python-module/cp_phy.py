#
# PROFIBUS DP - Communication Processor PHY access library
#
# Copyright (c) 2013 Michael Buesch <m@bues.ch>
#
# Licensed under the terms of the GNU General Public License version 2,
# or (at your option) any later version.
#

import sys
import time

try:
	from spidev import SpiDev
except ImportError:
	print("Failed to import 'spidev' module.")
	print("Get 'spidev' from git://git.bues.ch/py-spidev.git (branch 'py3')")
	sys.exit(1)
try:
	import RPi.GPIO as GPIO
except ImportError:
	print("Failed to import 'RPi.GPIO' module.")
	sys.exit(1)
except RuntimeError as e:
	print(str(e))
	sys.exit(1)


class PhyError(Exception):
	pass

class CpPhyMessage(object):
	RASPI_PACK_HDR_LEN	= 3

	# Message frame control
	RPI_PACK_NOP		= 0	# No operation
	RPI_PACK_RESET		= 1	# Reset
	RPI_PACK_SETCFG		= 2	# Set config
	RPI_PACK_PB_SDR		= 3	# Profibus SDR request
	RPI_PACK_PB_SDR_REPLY	= 4	# Profibus SDR reply
	RPI_PACK_PB_SDN		= 5	# Profibus SDN request
	RPI_PACK_ACK		= 6	# Short ACK
	RPI_PACK_NACK		= 7	# Short NACK
	__RPI_PACK_FC_MAX	= RPI_PACK_NACK

	def __init__(self, fc, payload=()):
		self.fc = fc
		self.payload = payload

	@staticmethod
	def calculateChecksum(packetData):
		return ((sum(packetData) - (packetData[2] & 0xFF)) ^ 0xFF) & 0xFF

	def getRawData(self):
		data = [ self.fc, len(self.payload), 0, ]
		data.extend(self.payload)
		data[2] = self.calculateChecksum(data)
		return data

	def setRawData(self, data):
		self.fc = data[0]
		if self.fc == self.RPI_PACK_NOP:
			return
		if len(data) < self.RASPI_PACK_HDR_LEN:
			raise PhyError("CpPhyMessage: Message too small")
		if self.calculateChecksum(data) != data[2]:
			raise PhyError("CpPhyMessage: Invalid checksum")
		self.payload = data[3:]
		if self.fc < 0 or self.fc > self.__RPI_PACK_FC_MAX:
			raise PhyError("CpPhyMessage: Unknown frame control %02X" %\
				self.fc)
		if len(self.payload) != data[1]:
			raise PhyError("CpPhyMessage: Invalid payload length")

class CpPhy(object):

	# Profibus baud-rates
	PB_PHY_BAUD_9600	= 0
	PB_PHY_BAUD_19200	= 1
	PB_PHY_BAUD_45450	= 2
	PB_PHY_BAUD_93750	= 3
	PB_PHY_BAUD_187500	= 4
	PB_PHY_BAUD_500000	= 5
	PB_PHY_BAUD_1500000	= 6
	PB_PHY_BAUD_3000000	= 7
	PB_PHY_BAUD_6000000	= 8
	PB_PHY_BAUD_12000000	= 9

	# GPIO numbers (BCM)
	GPIO_RESET		= 17
	GPIO_IRQ		= 27
	GPIO_SS			= 8
	GPIO_MISO		= 9
	GPIO_MOSI		= 10
	GPIO_SCK		= 11

	baud2id = {
		9600		: PB_PHY_BAUD_9600,
		19200		: PB_PHY_BAUD_19200,
		45450		: PB_PHY_BAUD_45450,
		93750		: PB_PHY_BAUD_93750,
		187500		: PB_PHY_BAUD_187500,
		500000		: PB_PHY_BAUD_500000,
		1500000		: PB_PHY_BAUD_1500000,
		3000000		: PB_PHY_BAUD_3000000,
		6000000		: PB_PHY_BAUD_6000000,
		12000000	: PB_PHY_BAUD_12000000,
	}

	def __init__(self, device=0, chipselect=0):
		self.device = device
		self.chipselect = chipselect

		try:
			# Initialize GPIOs
			GPIO.setmode(GPIO.BCM) # Use Broadcom numbers
			GPIO.setwarnings(False)
			GPIO.setup(self.GPIO_RESET, GPIO.OUT, initial=GPIO.LOW)
			GPIO.setup(self.GPIO_IRQ, GPIO.IN, pull_up_down=GPIO.PUD_OFF)
			GPIO.add_event_detect(self.GPIO_IRQ, GPIO.RISING)
			time.sleep(0.05)

			# Initialize SPI
			try:
				self.spi = SpiDev()
				self.spi.open(device, chipselect)
			except IOError as e:
				raise PhyError("Failed to open SPI device %d.%d: %s" %\
					(device, chipselect, str(e)))
			try:
				self.spi.mode = 0;
				self.spi.bits_per_word = 8;
				self.spi.cshigh = False
				self.spi.lsbfirst = False
				self.spi.max_speed_hz = 200000;
			except IOError as e:
				try:
					self.spi.close()
					self.spi = None
				except:
					pass
				raise PhyError("Failed to configure SPI device %d.%d: %s" %\
					(device, chipselect, str(e)))

			# Get the controller out of hardware reset
			GPIO.output(self.GPIO_RESET, GPIO.HIGH)
			time.sleep(0.2)

			# Send a software reset
			self.sendReset()
		except:
			GPIO.cleanup()
			raise

	def cleanup(self):
		self.spi.close()
		self.spi = None
		GPIO.cleanup()

	def pollReply(self):
		if not GPIO.event_detected(self.GPIO_IRQ):
			return None
		reply = self.spi.readbytes(CpPhyMessage.RASPI_PACK_HDR_LEN)
		payloadLen = reply[1] & 0xFF
		if payloadLen:
			reply.extend(self.spi.readbytes(payloadLen))
		message = CpPhyMessage(0)
		message.setRawData(reply)
		return message

	def __sendMessage(self, message, sync=False):
		data = message.getRawData()
		self.spi.writebytes(data)
		if not sync:
			return
		while 1:
			reply = self.pollReply()
			if reply:
				return reply

	def sendReset(self):
		return self.__sendMessage(CpPhyMessage(CpPhyMessage.RPI_PACK_RESET),
					  sync=True)

	def profibusSetPhyConfig(self, baudrate):
		try:
			baudID = self.baud2id[baudrate]
		except KeyError:
			raise PhyError("Invalid baud-rate")
		payload = [ baudID ]
		message = CpPhyMessage(CpPhyMessage.RPI_PACK_SETCFG, payload)
		reply = self.__sendMessage(message, sync=True)
		pass#TODO

	def profibusSend_SDN(self, telegram, sync=False):
		return self.__sendMessage(CpPhyMessage(CpPhyMessage.RPI_PACK_PB_SDN,
						       telegram), sync)

	def profibusSend_SDR(self, telegram, sync=False):
		return self.__sendMessage(CpPhyMessage(CpPhyMessage.RPI_PACK_PB_SDR,
						       telegram), sync)