import socket

# Server information
server_ip = '192.168.1.50'  # Update with your server's IP address
server_port = 5005  # Update with the server's port number

# Create a TCP/IP client
client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client_socket.connect((server_ip, server_port))

while True:
    # Receive the available USB devices from the server
    device_info_str = client_socket.recv(1024).decode()
    device_info = eval(device_info_str)  # Convert the string representation back to a list

    # Display available USB devices
    print("Available USB devices:")
    for idx, (vendor_id, product_id) in enumerate(device_info):
        print(f"[{idx}] Vendor ID: {hex(vendor_id)}, Product ID: {hex(product_id)}")

    # Let the user select the USB devices to connect
    selected_devices = input("Enter the indexes of the devices to connect (comma-separated), or 'q' to quit: ")

    if selected_devices.lower() == 'q':
        break

    selected_devices = [int(idx) for idx in selected_devices.split(",")]

    # Send the selected devices to the server
    client_socket.send(str(selected_devices).encode())

    # Receive the response from the server
    response = client_socket.recv(1024).decode()

    # Check if the devices were connected successfully
    if response == "success":
        print("Devices connected successfully.")
    else:
        print("Failed to connect devices.")

# Close the client socket
client_socket.close()
