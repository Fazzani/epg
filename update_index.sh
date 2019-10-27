import os
import json

if __name__ == "__main__":
    d = {"name": f.lower() for f in os.listdir("./logosChannels")}
    with open("./index.json", "r") as f:
        f.write(json.dump(d))
