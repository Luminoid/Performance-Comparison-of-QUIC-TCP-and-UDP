# Performance Comparison of QUIC, TCP and UDP

## Team members
Rick Li (cl6405), Nini Lin (hl4674)

## Dependency
### Mininet
``` bash
sudo apt-get install mininet
```

## Run Experiment
The experiment is tested on Ubuntu 22.04. It takes around 30 minutes to run all the experiments. You can also comment out some experiments in `topo.py` to save time.
``` bash
sudo mn -c
sudo killall iperf3
sudo python3 topo.py
```

## Support BBR
`/etc/sysctl.conf`: Add the following lines
``` bash
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr
```
Reload configurations: `sudo sysctl -p`
Check supported congestion control algorithms: `/sbin/sysctl net.ipv4.tcp_available_congestion_control`

## Content
`topo.py`: The main experiment code
`ExperimentResults.numbers`: Experiment data and graphs
`Project.key`: Slides for the presentation
`server.key` & `server.crt`: certification file used by QUIC
`Results/`: All the experiment results

## Updates after final presentation
1. Support TCP BBR in Single Bottleneck Single Client experiment and Parking Lot Multi Client experiment
2. Experiment 4: Run TCP Cubic and QUIC Cubic in the same network (Parking Lot Topo) and compare which one has better performance
3. Refactor the code
4. Fix an error in the QUIC establishment calculation
