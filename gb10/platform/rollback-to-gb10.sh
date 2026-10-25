#!/bin/bash
# Puts the box back on the gb10 build: its desk on 8090 and its cron loop.
for p in $(ps -eo pid,args | awk "\$2==\"python3\" && (\$3==\"app/server.py\" || \$3==\"channel/dashboard.py\" || \$3==\"channel/agent_desk.py\") {print \$1}"); do kill $p; done
crontab ~/crontab.gb10-build.bak
cd ~/ft-hack && setsid nohup python3 channel/dashboard.py --host 0.0.0.0 --port 8090 >> logs/dashboard.log 2>&1 < /dev/null &
echo "back on the gb10 build"
