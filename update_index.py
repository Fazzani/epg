import os
import json

if __name__ == "__main__":
    d = {os.path.splitext(f.lower())[0]:f for f in os.listdir("./logosChannels")}
    with open("./index_picons.json", "w+") as f:
        f.write(json.dumps(d))
