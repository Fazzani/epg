#!/bin/bash

source $HOME/.keychain/${HOSTNAME}-sh
date +"%m/%d/%Y %H:%M:%S $HOSTNAME"
cd /root/epg/
git fetch origin
reslog=$(git log HEAD..origin/master --oneline)
if [[ "${reslog}" != "" ]] ; then
	git pull && 
	mkdir ./logosChannels/tmp 
	#ls ./logosChannels/tmp/
	echo "Triming png picons" 
	mogrify -trim -path  ./logosChannels/tmp  ./logosChannels/* &&
	mv  ./logosChannels/tmp/* ./logosChannels/  
	rm -R  ./logosChannels/tmp 
	echo "Pushing to Github" 
	git add --all && 
	git commit -m "Triming picons" && git push
else
 echo 'nothing to do..'
fi
