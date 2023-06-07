import socket
import usb.core
import usb.util
import threading

# Server information
server_ip = '192.168.1.50'  # Update with your server's IP address
server_port = 5005  # Update with the server's port number

# Function to handle client connections
def handle_client_connection(client_socket, address):
    # Find available USB devices
    devices = usb.core.find(find_all=True)

    # Extract vendor and product IDs of the USB devices
    device_info = [(device.idVendor, device.idProduct) for device in devices]

    # Send the USB device information to the client
    client_socket.send(str(device_info).encode())

    while True:
        # Receive the selected devices from the client
        selected_devices_str = client_socket.recv(1024).decode()
        selected_devices = eval(selected_devices_str)  # Convert the string representation back to a list

        if not selected_devices:
            break

        # Connect to the selected USB devices
        success = True
        for idx in selected_devices:
            vendor_id, product_id = device_info[idx]
            device = usb.core.find(idVendor=vendor_id, idProduct=product_id)
            if device is not None:
                try:
                    # Implement the necessary code to connect to the selected USB devices and handle input events
                    usb.util.claim_interface(device, 0)  # Claim the interface
                    # Additional code for device connection and event handling goes here
                except usb.core.USBError as e:
                    print(f"Failed to connect to device - Vendor ID: {hex(vendor_id)}, Product ID: {hex(product_id)}")
                    success = False
            else:
                print(f"Device not found - Vendor ID: {hex(vendor_id)}, Product ID: {hex(product_id)}")
                success = False

        if success:
            client_socket.send(b"success")
        else:
            client_socket.send(b"failure")

    # Close the client socket
    client_socket.close()

# Create a TCP/IP server socket
server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

# Bind the server socket to a specific address and port
server_socket.bind((server_ip, server_port))

# Listen for incoming connections
server_socket.listen(1)

print(f"Server is listening on {server_ip}:{server_port}")

while True:
    # Accept a client connection
    client_socket, address = server_socket.accept()
    print(f"Client connected: {address[0]}:{address[1]}")

    # Start a new thread to handle the client connection
    thread = threading.Thread(target=handle_client_connection, args=(client_socket, address))
    thread.start()
