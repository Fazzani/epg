#!/bin/bash
#git pull && 
#mkdir ./logosChannels/tmp 
echo "Triming png picons" 
mogrify -trim -Path  ./logosChannels/tmp  ./logosChannels/* &&
mv  ./logosChannels/tmp/*  ./logosChannels/ && rm -R  ./logosChannels/tmp && 
echo "Pushing to Github" 
git add --all && 
git commit -m "Triming picons" && git push

