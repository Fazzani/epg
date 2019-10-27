#!/usr/bin/env python

import os
import json


if __name__ == "__main__":
   dict = {"name": f.lower() for f in os.listdir("./logosChannels")}
    with open("./index.json", "r") as f:
        f.write(json.dump(dict))
