import os
import json


PICON_BASE_URL = os.getenv("PICON_BASE_URL", "https://raw.githubusercontent.com/Fazzani/epg/master/logosChannels/")


if __name__ == "__main__":
    d = {os.path.splitext(f.lower())[0]:f"{PICON_BASE_URL}{f}" for f in os.listdir("./logosChannels")}
    with open("./index_picons.json", "w+") as f:
        f.write(json.dumps(d))
