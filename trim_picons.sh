#!/bin/bash

echo "Triming png picons"
mogrify -trim  -resize 250x200 -gravity Center -extent 250x200 -path ./logoChannels/*.*
echo "Pushing to Github"
git add --all && git commit -m "Triming picons" && git push

