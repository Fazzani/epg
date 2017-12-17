#!/bin/bash

echo "Triming png picons"
mogrify -trim *.png 
echo "Pushing to Github"
git add --all && git commit -m "add cropped" && git push

