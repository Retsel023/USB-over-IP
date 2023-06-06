import socket
import usb.core
import usb.util
import threading

# Create a TCP/IP server
server_ip = '0.0.0.0'  # Update with your server's IP address
server_port = 5005  # Update with your desired port number

server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server_socket.bind((server_ip, server_port))
server_socket.listen(5)

# Function to find all connected USB devices
def find_usb_devices():
    devices = usb.core.find(find_all=True)
    return devices

# Function to handle client connections
def handle_client_connection(client_socket, device):
    try:
        while True:
            # Transfer USB data to the client
            data = device.read(1, 8)
            client_socket.send(data)
    except usb.core.USBError as e:
        print(f"USB Error: {str(e)}")
    finally:
        client_socket.close()

# Function to accept and handle client connections
def accept_client_connections():
    while True:
        # Accept client connections
        client_socket, _ = server_socket.accept()
        print("Client connected.")

        # Find all connected USB devices
        devices = find_usb_devices()

        # Start a thread for each connected USB device
        for device in devices:
            t = threading.Thread(target=handle_client_connection, args=(client_socket, device))
            t.start()

# Start accepting client connections
print("Waiting for clients...")
accept_client_connections()
