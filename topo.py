from mininet.net import Mininet
from mininet.topo import Topo
from mininet.node import CPULimitedHost     # Exception: cgroups not mounted on /sys/fs/cgroup
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.util import dumpNodeConnections
from mininet.log import setLogLevel

import os
import re
import time
from functools import partial


testTime = 10
sleepTime = 15
singleBottleneckClientCount = 20
parkingLotClientCount = 60


class SingleBottleneckTopo(Topo):
    def __init__(self, n=10):
        Topo.__init__(self)

        # Add Switch
        s1 = self.addSwitch("s1")

        # Add Hosts
        for h in range(n):
            host = self.addHost(f"h{h+1}")
            self.addLink(host, s1)


class ParkingLotTopo(Topo):
    def __init__(self, n, num_bottlenecks, delay, loss):
        Topo.__init__(self)

        # Add switches
        switches = []
        for i in range(num_bottlenecks + 1):
            switch = self.addSwitch(f's{i+1}')
            switches.append(switch)
            if i > 0:
                self.addLink(switches[i-1], switch, bw=500, delay=f"{delay}ms", loss=loss, max_queue_size=1000, use_htb=True)

        # Add hosts
        for i, switch in enumerate(switches[:-1]):
            for j in range(n):
                host = self.addHost(f'h{j+1}_{i+1}')
                self.addLink(host, switch, bw=100, delay="2ms", use_htb=True)

        # Add servers
        server1 = self.addHost("server1")
        self.addLink(server1, switches[0], bw=500, delay="2ms", use_htb=True)
        server2 = self.addHost("server2")
        self.addLink(server2, switches[5], bw=500, delay="2ms", use_htb=True)


def runServerCommand(server, protocol, serverPort):
    if protocol == "TCP" or protocol == "UDP":
        command = f"iperf3 -s -p {str(serverPort)} -D"
    elif protocol == "QUIC":
        command = f"qperf -s -p {str(serverPort)} &"
    server.cmd(command)


def runClientCommand(client, server, protocol, serverPort, cc_param, output_file):
    command_dict = {"TCP": f"iperf3 -c {server.IP()} -p {serverPort} -t {testTime} -V -C {cc_param} > {output_file} &",
                    "UDP": f"iperf3 -c {server.IP()} -p {serverPort} -t {testTime} -u -b 10M -V > {output_file} &",
                    "QUIC": f"qperf -c {server.IP()} -p {serverPort} -t {testTime} --cc {cc_param} > {output_file} &"}
    client.cmd(command_dict.get(protocol, ""))


def analyzeTCPData(client_output_files):
    retr_sum = 0
    retr_sum_num = 0
    throughput_sum = 0
    failure_count = 0

    for output_file in client_output_files:
        if "TCP" not in output_file:
            continue
        with open(output_file) as f:
            lines = f.readlines()
        current_throughput = 0
        for line in lines:
            if 'bits/sec' in line and 'sender' in line:
                retr_sum += int(re.findall(r'\d+', line)[-1])
                retr_sum_num += 1
            elif 'bits/sec' in line and 'receiver' in line:
                if 'Mbits/sec' in line:
                    current_throughput += float(re.findall(r'\d+\.\d+', line)[-1]) * 1000
                elif 'Kbits/sec' in line:
                    current_throughput += float(re.findall(r'\d+', line)[-1])
        if current_throughput == 0:
            failure_count += 1
        throughput_sum += current_throughput

    print('TCP Average RetrSum:', retr_sum / retr_sum_num if retr_sum_num > 0 else 0)
    print('TCP Network throughput (Kbps):', throughput_sum)
    print('TCP Failure count:', failure_count)


def analyzeUDPData(client_output_files, client_count):
    total_throughput = 0
    total_packet_loss_rate = 0
    total_jitter = 0
    failure_count = 0
    jitter_count = 0

    for output_file in client_output_files:
        if "UDP" not in output_file:
            continue
        with open(output_file) as f:
            lines = f.readlines()
        throughput = 0
        for line in lines:
            if 'bits/sec' in line and 'receiver' in line:
                # Take the third last value as the bitrate, convert it to Kbits/sec if it's in Mbits/sec
                match1 = re.search(r'(\d+\.\d+)\s*Mbits/sec', line)
                match2 = re.search(r'(\d+)\s*Kbits/sec', line)
                if match1:
                    throughput = float(match1.group(1)) * 1000
                elif match2:
                    throughput = float(match2.group(1))
                total_throughput += throughput

                match3 = re.search(r'(\d+(\.\d+)?)\s*ms', line)
                if match3:
                    jitter_count += 1
                    jitter = float(match3.group(1))
                    total_jitter += jitter

                match4 = re.search(r'\((\d+(\.\d+)?)%\)', line)
                packet_loss_rate = 100
                if match4:
                    packet_loss_rate = float(match4.group(1))
                total_packet_loss_rate += packet_loss_rate
        if throughput == 0:
            failure_count += 1
            packet_loss_rate = 100
            total_packet_loss_rate += packet_loss_rate
    average_jitter = total_jitter / jitter_count if jitter_count > 0 else 0

    print('UDP Network throughput (Kbps): ', total_throughput)
    print('UDP Average Packet Loss Rate (%):', total_packet_loss_rate / client_count)
    print('UDP Average Jitter (ms):', average_jitter)
    print('UDP Failure count: ', failure_count)


def analyzeQUICData(client_output_files):
    establishment_time = 0
    establishment_time_num = 0
    time_to_first_byte = 0
    time_to_first_byte_num = 0
    sum_of_bytes_received = 0
    failure_count = 0

    for output_file in client_output_files:
        if "QUIC" not in output_file:
            continue
        with open(output_file) as f:
            lines = f.readlines()
        sum_of_host_bytes = 0
        for line in lines:
            if 'connection establishment time:' in line:
                establishment_time += int(re.findall(r'\d+', line)[0])
                establishment_time_num += 1
            elif 'time to first byte:' in line:
                time_to_first_byte += int(re.findall(r'\d+', line)[0])
                time_to_first_byte_num += 1
            elif 'second' in line:
                bytes_received = int(re.findall(r'\d+', line)[-1])
                sum_of_host_bytes += bytes_received
        if sum_of_host_bytes == 0:
            failure_count += 1
        sum_of_bytes_received += sum_of_host_bytes

    print('QUIC Average establishment time:', establishment_time / establishment_time_num if establishment_time_num > 0 else 0)
    print('QUIC Average time to first byte:', time_to_first_byte / time_to_first_byte_num if time_to_first_byte_num > 0 else 0)
    print('QUIC Network throughput (Kbps):', sum_of_bytes_received * 8 / 1024 / testTime)
    print('QUIC Failure count: ', failure_count)


def analyzeData(protocol, client_output_files, client_count):
    if protocol == "TCP":
        analyzeTCPData(client_output_files)
    elif protocol == "UDP":
        analyzeUDPData(client_output_files, client_count)
    elif protocol == "QUIC":
        analyzeQUICData(client_output_files)
    elif protocol == "TCP+QUIC":
        analyzeTCPData(client_output_files)
        analyzeQUICData(client_output_files)


def testNet(net, protocol, isMultiClients, topoName, delay, loss, cc=""):
    # CLI(net)
    net.start()

    cc_param = cc if cc else "cubic" if protocol == "TCP" else "reno"
    client_count = singleBottleneckClientCount if topoName == "SingleBottleneck" else parkingLotClientCount

    if not isMultiClients:
        h1, h2 = net.get( "h1", "h2" )
        # dumpNodeConnections(net.hosts)

        if protocol == "TCP":
            print(h1.cmd("iperf3 -s &"))
            time.sleep(2)
            print(h2.cmd(f"iperf3 -c {h1.IP()} -t {testTime} -V -C {cc_param}"))
        elif protocol == "UDP":
            print(h1.cmd("iperf3 -s -D"))
            time.sleep(2)
            print(h2.cmd(f"iperf3 -c {h1.IP()} -t {testTime} -u -b 10M -V"))
        elif protocol == "QUIC":
            print(h1.cmd(f"qperf -s --cc {cc_param} &"))
            time.sleep(2)
            output_file = f"./Results/QUICSingleBottleneckSingleClient.txt"
            open(output_file, "w")
            h2.cmd(f"qperf -c {h1.IP()} -t {testTime} --cc {cc_param} > {output_file} &")
            time.sleep(sleepTime)
            analyzeData(protocol, [output_file], client_count)
    else:
        # dumpNodeConnections(net.hosts)
        basePort = 5000
        client_output_files = []

        clients = []
        if topoName == "SingleBottleneck":
            server = net.get("h1")
            clients = [net.get(f"h{i}") for i in range(2, client_count + 2)] # h2-h21

            for i in range(0, client_count):
                serverPort = basePort + i
                runServerCommand(server, protocol, serverPort)
        elif topoName == "ParkingLot":
            server1 = net.get("server1")
            server2 = net.get("server2")
            for i in range(1, 4):
                clients.extend([net.get(f"h{j}_{i}") for j in range(1, 11)])
            for i in range(4, 7):
                clients.extend([net.get(f"h{j}_{i}") for j in range(1, 11)])

            for i in range(0, int(client_count / 2)):
                serverPort = basePort + i
                p = "TCP" if protocol == "TCP+QUIC" else protocol
                runServerCommand(server2, p, serverPort)
            for i in range(int(client_count / 2), client_count):
                serverPort = basePort + i
                p = "QUIC" if protocol == "TCP+QUIC" else protocol
                runServerCommand(server1, p, serverPort)

        time.sleep(2)
        if protocol == "TCP+QUIC":
            os.makedirs(f"./Results/TCP{topoName}_{delay}_{loss}", exist_ok=True)
            os.makedirs(f"./Results/QUIC{topoName}_{delay}_{loss}", exist_ok=True)
        else:
            os.makedirs(f"./Results/{protocol}{topoName}_{delay}_{loss}", exist_ok=True)
        # TODO: If running both TCP and QUIC in the same work, 
        # the clients should be executing one by one from each side.
        for index, client in enumerate(clients):
            serverPort = basePort + index
            p = protocol
            if protocol == "TCP+QUIC":
                p = "TCP" if index < client_count / 2 else "QUIC"
            output_file = f"./Results/{p}{topoName}_{delay}_{loss}/{p}_output_{serverPort}_{client.name}.txt"
            open(output_file, "w")
            client_output_files.append(output_file)
            if topoName == "ParkingLot":
                server = server2 if index < client_count / 2 else server1
            runClientCommand(client, server, p, serverPort, cc_param, output_file)
            time.sleep(1)
        time.sleep(sleepTime)

        analyzeData(protocol, client_output_files, client_count)
    net.stop()


def run(topoType, protocol, isMultiClients, cc = ""):
    delaySet = []   # [0, 5, 20, 100, 500, 1000]
    lossSet = [0, 2, 5, 10, 20, 40] # [0, 2, 5, 10, 20, 40]

    conditions = [(delay, 2) for delay in delaySet] + [(5, loss) for loss in lossSet]
    for delay, loss in conditions:
        if topoType == "SingleBottleneck":
            topo = SingleBottleneckTopo(n=21)
            net = Mininet(topo=topo, link=partial(TCLink, bw=100, delay="%sms" % delay, loss=loss, max_queue_size=1000, use_htb=True))
            print(f"====== Single Bottleneck, Bandwidth: 100Mbps, Delay: {delay}ms, Packet Loss: {loss}% ======")
            testNet(net, protocol, isMultiClients, "SingleBottleneck", delay, loss, cc = cc)
        elif topoType == "ParkingLot":
            topo = ParkingLotTopo(n=10, num_bottlenecks=6, delay=delay, loss=loss)
            net = Mininet(topo=topo, link=TCLink)
            print(f"====== Parking Lot, Bandwidth: 100Mbps, Delay: {delay}ms, Packet Loss: {loss}% ======")
            testNet(net, protocol, isMultiClients, "ParkingLot", delay, loss, cc = cc)
            

if __name__ == "__main__":
    setLogLevel("info")
    # Experiment 1
    run(topoType="SingleBottleneck", protocol="TCP", isMultiClients = False)
    run(topoType="SingleBottleneck", protocol="TCP", isMultiClients = False, cc = "bbr")
    run(topoType="SingleBottleneck", protocol="UDP", isMultiClients = False)
    run(topoType="SingleBottleneck", protocol="QUIC", isMultiClients = False)
    run(topoType="SingleBottleneck", protocol="QUIC", isMultiClients = False, cc = "cubic")

    # Experiment 2
    run(topoType="SingleBottleneck", protocol="TCP", isMultiClients = True)
    run(topoType="SingleBottleneck", protocol="UDP", isMultiClients = True)
    run(topoType="SingleBottleneck", protocol="QUIC", isMultiClients = True, cc = "cubic")

    # Experiment 3
    run(topoType="ParkingLot", protocol="TCP", isMultiClients = True)
    run(topoType="ParkingLot", protocol="TCP", isMultiClients = True, cc = "bbr")
    run(topoType="ParkingLot", protocol="UDP", isMultiClients = True)
    run(topoType="ParkingLot", protocol="QUIC", isMultiClients = True, cc = "cubic")

    # Experiment 4
    run(topoType="ParkingLot", protocol="TCP+QUIC", isMultiClients = True, cc = "cubic")
