"""
LightBurn UDP Communication Module

This module provides a class for communicating with LightBurn software via UDP.
Supports sending commands, checking status, loading files, and managing the application.
"""
__version__ = "1.0.0"
__author__ = "Urs Helfenstein"

import socket
import subprocess
import time
import os

__all__ = ['LightBurnUDPCommunication']

class LightBurnUDPCommunication:
    """
    A class to handle UDP communication with LightBurn software.
    Provides methods to send messages, ping, and automatically start LightBurn if needed.
    """

    def __init__(self, lightburn_path, udp_ip="127.0.0.1", udp_out_port=19840, udp_in_port=19841):
        """
        Initialize the LightBurn UDP communication handler.

        Args:
            lightburn_path (str): Full path to the LightBurn executable (mandatory)
            udp_ip (str): IP address for UDP communication (default: "127.0.0.1")
            udp_out_port (int): Port for outgoing messages to LightBurn (default: 19840)
            udp_in_port (int): Port for incoming messages from LightBurn (default: 19841)

        Raises:
            FileNotFoundError: If the LightBurn executable is not found
        """
        self.lightburn_path = lightburn_path
        self.udp_ip = udp_ip
        self.udp_out_port = udp_out_port
        self.udp_in_port = udp_in_port

        # Validate LightBurn path
        if not os.path.exists(lightburn_path):
            raise FileNotFoundError(f"LightBurn executable not found at: {lightburn_path}")

    def send_message(self, message, timeout=1.0):
        """
        Send a UDP message to LightBurn and wait for a response.

        Args:
            message (str): The message to send to LightBurn
            timeout (float): Timeout in seconds to wait for response (default: 1.0)

        Returns:
            str or False: Returns the response message if received, False if timeout or error
        """
        try:
            # Create sockets
            out_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            in_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

            # Set timeout for receiving
            in_sock.settimeout(timeout)

            # Bind the input socket
            in_sock.bind((self.udp_ip, self.udp_in_port))

            # Send the message
            out_sock.sendto(message.encode("utf-8"), (self.udp_ip, self.udp_out_port))

            try:
                # Wait for response
                data, addr = in_sock.recvfrom(1024)
                response = data.decode('utf-8')
                return response
            except socket.timeout:
                return False

        except Exception as e:
            print(f"Error in UDP communication: {e}")
            return False
        finally:
            # Clean up sockets
            try:
                out_sock.close()
                in_sock.close()
            except:
                pass

    def ping(self, timeout=1.0):
        """
        Send a PING message to LightBurn.

        Args:
            timeout (float): Timeout in seconds to wait for response (default: 1.0)

        Returns:
            bool: True if LightBurn responds with 'OK', False otherwise
        """
        response = self.send_message("PING", timeout)
        return response == "OK"

    def start_lightburn(self):
        """
        Start the LightBurn application.

        Returns:
            bool: True if started successfully, False otherwise
        """
        try:
            subprocess.Popen([self.lightburn_path], shell=False)
            print(f"Started LightBurn from: {self.lightburn_path}")
            return True
        except Exception as e:
            print(f"Error starting LightBurn: {e}")
            return False

    def ensure_lightburn_running(self, startup_timeout=10.0):
        """
        Ensure LightBurn is running and responding to PING.
        If not responding, start LightBurn and wait for it to be ready.

        Args:
            startup_timeout (float): Maximum time in seconds to wait for LightBurn to start (default: 10.0)

        Returns:
            bool: True if LightBurn is responding, False if timeout or error
        """
        # First, try to ping LightBurn
        if self.ping():
            print("LightBurn is already running and responding")
            return True

        print("LightBurn not responding, attempting to start...")

        # Start LightBurn
        if not self.start_lightburn():
            return False

        # Wait for LightBurn to start and respond
        start_time = time.time()

        while time.time() - start_time < startup_timeout:
            print("Sending PING to LightBurn...")

            if self.ping():
                print("LightBurn started successfully and is responding")
                return True

            # Wait 1 second before next ping
            time.sleep(1.0)

        print(f"Timeout: LightBurn did not respond within {startup_timeout} seconds")
        return False

    def get_status(self, timeout=1.0):
        """
        Get the status from LightBurn.

        Args:
            timeout (float): Timeout in seconds to wait for response (default: 1.0)

        Returns:
            str or False: Returns "Running" if a job is executing, "Idle" if software is running but no job is executed, False if no response or error
        """
        response = self.send_message("STATUS", timeout)

        if response == "!":
            return "Running"
        elif response == "OK":
            return "Idle"
        else:
            return response

    def load_file(self, file_path, force=False, timeout=1.0):
        """
        Load a file in LightBurn.

        Args:
            file_path (str): Path to the file to load
            force (bool): Whether to force load the file (default: False)
            timeout (float): Timeout in seconds to wait for response (default: 1.0)

        Returns:
            str or False: Returns the response if received, False otherwise
        """
        command = "FORCELOAD" if force else "LOADFILE"
        message = f"{command}:{file_path}"
        return self.send_message(message, timeout)

    def start_job(self, timeout=1.0):
        """
        Start the current job in LightBurn.

        Args:
            timeout (float): Timeout in seconds to wait for response (default: 1.0)

        Returns:
            str or False: Returns the response if received, False otherwise
        """
        return self.send_message("START", timeout)

    def close_lightburn(self, force=False, timeout=1.0):
        """
        Close LightBurn.

        Args:
            force (bool): Whether to force close LightBurn (default: False)
            timeout (float): Timeout in seconds to wait for response (default: 1.0)

        Returns:
            str or False: Returns the response if received, False otherwise
        """
        command = "FORCECLOSE" if force else "CLOSE"
        return self.send_message(command, timeout)

    def __str__(self):
        """String representation of the LightBurnUDPCommunication object."""
        return (f"LightBurnUDPCommunication(path='{self.lightburn_path}', "
                f"ip='{self.udp_ip}', out_port={self.udp_out_port}, in_port={self.udp_in_port})")

    def __repr__(self):
        """Detailed representation of the LightBurnUDPCommunication object."""
        return self.__str__()


# Example usage and standalone functionality
if __name__ == "__main__":
    # Example path - adjust as needed for your system
    lightburn_exe_path = r"E:\Program Files\LightBurn\LightBurn.exe"

    try:
        # Create LightBurn communication object with default settings
        lb_comm = LightBurnUDPCommunication(lightburn_exe_path)
        print(f"Created: {lb_comm}")

        # Test basic ping
        print("\n=== Testing basic ping ===")
        if lb_comm.ping():
            print("✓ LightBurn is responding to PING")
        else:
            print("✗ LightBurn is not responding to PING")

        # Test ensure running (will start LightBurn if needed)
        print("\n=== Ensuring LightBurn is running ===")
        if lb_comm.ensure_lightburn_running(startup_timeout=15.0):
            print("✓ LightBurn is now running and responding")

            # Test status commands
            print("\n=== Testing STATUS command ===")
            status = lb_comm.get_status()
            print(f"Status: {status}")

            # print("\n=== Testing FORCELOAD command ===")
            # file_path = r"E:\pCloud\MedManna\20 Products\55 Deo\20 Shipping\PillowBox-originalSize-Design19.lbrn2"
            # response = lb_comm.load_file(file_path, force=True)
            # if response:
            #     print("✓ File loaded successfully: " + file_path)
            # else:
            #     print("✗ Failed to load file: " + file_path)
            #
            # print("\n=== Testing LOAD command ===")
            # file_path = r"E:\pCloud\MedManna\20 Products\55 Deo\20 Shipping\PillowBox-originalSize-Design19.lbrn2"
            # response = lb_comm.load_file(file_path)
            # if response:
            #     print("✓ File loaded successfully: " + file_path)
            # else:
            #     print("✗ Failed to load file: " + file_path)
            #
            # print("\n=== Testing CLOSE command ===")
            # response = lb_comm.close_lightburn()
            # if response:
            #     print("✓ Lightburn closed successfully")
            # else:
            #     print("✗ Failed to close lightburn")


        else:
            print("✗ Failed to start or connect to LightBurn")

    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Please adjust the lightburn_exe_path variable to match your LightBurn installation.")
    except Exception as e:
        print(f"Unexpected error: {e}")