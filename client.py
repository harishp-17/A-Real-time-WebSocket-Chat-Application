import asyncio
import websockets
import json
import requests
import sys
from datetime import datetime

class ChatterboxClient:
    def __init__(self):
        self.username = None
        self.websocket = None
        self.server_url = "ws://localhost:8000/ws/"
        self.api_url = "http://localhost:8000"
    
    def print_menu(self):
        print("\n" + "="*50)
        print(" CHOOSE OPTION:")
        print("1. Register New User")
        print("2. Login Existing User")
        print("═"*50)
    
    async def register(self):
        print("ENTER NEW USERNAME: ", end="", flush=True)
        username = input().strip()
        print("ENTER PASSWORD: ", end="", flush=True)
        password = input().strip()
        
        try:
            response = requests.post(f"{self.api_url}/register", 
                                   json={"username": username, "password": password})
            if response.status_code == 200:
                print("REGISTERED SUCCESSFULLY!")
                self.username = username
                return True
            print("ERROR:", response.json()["detail"])
        except Exception as e:
            print("CONNECTION ERROR:", e)
        return False
    
    async def login(self):
        print("ENTER USERNAME: ", end="", flush=True)
        username = input().strip()
        print("ENTER PASSWORD: ", end="", flush=True)
        password = input().strip()
        
        try:
            response = requests.post(f"{self.api_url}/login", 
                                   json={"username": username, "password": password})
            if response.status_code == 200:
                print("LOGGED IN SUCCESSFULLY!")
                self.username = username
                return True
            print("ERROR:", response.json()["detail"])
        except Exception as e:
            print("CONNECTION ERROR:", e)
        return False
    
    async def authenticate(self):
        while True:
            self.print_menu()
            print("YOUR CHOICE (1 or 2): ", end="", flush=True)
            choice = input().strip()
            
            if choice == "1":
                if await self.register():
                    return True
            elif choice == "2":
                if await self.login():
                    return True
            else:
                print("INVALID CHOICE! Try 1 or 2")
            input("\nPress Enter to continue...")
    
    async def receive_messages(self):
        try:
            async for message in self.websocket:
                data = json.loads(message)
                if data.get("type") == "history":
                    print(f"\nCHAT HISTORY ({len(data['messages'])} messages):")
                    for msg in data['messages']:
                        ts = msg['timestamp'][:19].replace('T', ' ')
                        print(f"   [{ts}] {msg['username']}: {msg['message']}")
                elif data.get("type") == "message":
                    ts = datetime.now().strftime("%H:%M:%S")
                    print(f"\n[{ts}] {data['data']['username']}: {data['data']['message']}")
                else:
                    print(f"\n {message}")
                print(" You (" + self.username + "): ", end="", flush=True)
        except:
            pass
    
    async def send_messages(self):
        while True:
            try:
                msg = await asyncio.get_event_loop().run_in_executor(None, input, "")
                if msg.lower().strip() in ['quit', 'exit', 'bye']:
                    break
                if msg.strip():
                    await self.websocket.send(msg.strip())
            except:
                break
    
    async def run(self):
        print("CHATTERBOX - Real-time Chat Application")
        print("Server: http://localhost:8000")
        
        if await self.authenticate():
            uri = self.server_url + self.username
            try:
                async with websockets.connect(uri) as websocket:
                    self.websocket = websocket
                    print(f"\nCONNECTED AS '{self.username}'!")
                    print("Type messages below ('quit' to exit)\n")
                    print("Loading chat history...\n")
                    
                    await asyncio.gather(
                        self.receive_messages(),
                        self.send_messages()
                    )
            except Exception as e:
                print(f"\nCONNECTION FAILED: {e}")
        print(" Goodbye!")

if __name__ == "__main__":
    try:
        client = ChatterboxClient()
        asyncio.run(client.run())
    except KeyboardInterrupt:
        print("\nGoodbye!")
    except Exception as e:
        print(f"\n ERROR: {e}")
