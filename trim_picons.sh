#!/bin/bash

cd  logosChannels/
echo "Triming png picons"
mogrify -trim *.png 
echo "Pushing to Github"
git add --all && git commit -m "Triming picons" && git push

