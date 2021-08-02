#!/bin/python3

"""
   RIP daemon     COSC364      RFC 2453
   Please include router configuration file in the same directory as this daemon.py

   Author: Changxing Gong, Bowen Jiang
   Date: April 2021

"""

import sys, socket, select, time, random, datetime

ROUTING_TABLE = {} # a diactionary of router_id: [next_hop_id, metrics, timer]
INPUT_PORTS = []
INPUT_SOCKETS = []
LINKS = {} # router_id : (port, distance)
ROUTER_ID = 0
PERIODIC_UPDATE_TIMER = 30 # 30 seconds  RFC 2453
TIMEOUT = 180 # 180 seconds  RFC 2453
GARBAGE_COLL_TIMER = 120 # 120 seconds  RFC 2453


def parse_conf_file():
    """Read sys.argv[1] configuration file,
       raise exception if the file cannot be read or is corrupted
    """
    global INPUT_PORTS, OUTPUT_ADDRESS, ROUTER_ID
    try:
        f = open(sys.argv[1])
        lines = f.readlines()
    except:
        sys.exit("Cannot read the configuration file")

    router_info = []
    for line in lines:
        if line[0] == '#' or line[0] == '\n':
            continue
        else:
            router_info.append(line)

    # get router ID from configuration file
    try:
        if "router-id" not in router_info[0]:
            raise ValueError
        router_id = ''
        for char in router_info[0].strip("router-id ").rstrip("\n"):
            if char == '#':
                break
            else:
                router_id += char
            ROUTER_ID = int(router_id)
            if ROUTER_ID not in range(1, 64000+1):
                raise ValueError
    except:
        sys.exit('Keyword "router-id" is mandatory and Router ID should be in range [1, 64000]')

    # get input ports from configuration file
    try:
        if "input-ports" not in router_info[1]:
            raise ValueError
        input_ports = ''
        for char in router_info[1].strip("input-ports ").rstrip("\n"):
            if char == '#':
                break
            else:
                input_ports += char
        input_ports = input_ports.split(',')

        for i in range(len(input_ports)):
            input_ports[i] = int(input_ports[i])
            if input_ports[i] not in range(1024, 64000+1):
                raise ValueError
        if len(input_ports) != len(set(input_ports)):
            raise ValueError
        INPUT_PORTS = input_ports

    except:
        sys.exit('Keyword "input-ports" is mandatory, input ports should be unique AND in range [1024, 64000]')

    # get the output ports from configuration file
    try:
        if "outputs" not in router_info[2]:
            raise ValueError
        outputs = ''
        for char in router_info[2].strip("outputs ").rstrip("\n"):
            if char == '#':
                break
            else:
                outputs += char
        outputs = outputs.split(',')
        peer_input_port_set = set()
        for link in outputs:
            #split the outpot port to tuple and add to LINKS
            peer_input_port, metric, peer_router_id = [int(num) for num in link.split('-')]
            peer_input_port_set.add(peer_input_port)
            if metric > 16 or metric < 0:
                raise ValueError
            LINKS[peer_router_id] =  (peer_input_port, metric)
        if len(LINKS) != len(peer_input_port_set):
            raise ValueError
    except:
        sys.exit('Keyword "outputs" is mandatory, output ports should be unique AND in range [1024, 64000],'+\
                 ' the metric should not larger than 15')

    print_config_info()


def get_rip_pkt(router_id_peer):
    """this function is for generating the response message
       entry: [dest_router_id, next_hop_id, metric, [timer_flag, time_stamp]]
    """

    pkt = bytearray([2, 2, 0, ROUTER_ID]) # set the package header with local router ID
    for router_id_dest, (next_hop_id, metrics, _) in ROUTING_TABLE.items():
        if next_hop_id == router_id_peer:
            metrics = 16 # add poison
        pkt += bytearray([0]*4 + [0, 0, 0, router_id_dest] + [0] * 8 + [0, 0, 0, metrics])

    return pkt


def parse_rip_pkt(received):
    """This function parses the received pkt
       Return (0, []) if there is an unexpected field value
       Otherwise return (router_id, entry), "entry" is a list of routing table entries
    """
    entry = []
    received = list(received)
    if received[0:3] != [2,2,0]:  #check the header is correct otherwise raise error
        print('A packet with a wrong header is dropped', end='\n\n')
        return 0, []

    else:
        next_hop = received[3]  #parsing the packet from which hop and store it
        received = received[4:]  #remove the pkt header and get the left entity
        for i in range(0,len(received),20): #parsing the all entity, split by 20 byte per step
            entry.append((received[i+7], received[i+19]))  #each tuple contain router_id_dest and metric
            if received[i+19] not in range(0, 17):
                print('A packet with a wrong metric field is dropped', end='\n\n')
                return 0, []

    return next_hop, entry


def bind_sockets():
    """bind input ports from configuration file with UDP sockets"""
    try:
        for input_port in INPUT_PORTS:
            new_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            new_socket.bind(('127.0.0.1', input_port)) #local host
            INPUT_SOCKETS.append(new_socket)
    except:
        sys.exit("Unable to bind specified port numbers to UDP socket(s)")


def listening_loop():
    """This function is for listening the packet from the network"""
    last_regular_time_up = time.perf_counter()
    print(f"{datetime.datetime.now()}  Sending the first routing table...", end='\n\n')
    send_routing_table()
    while True:
        readable, writble, excep = select.select(INPUT_SOCKETS, [], [], 1)
        for sock in readable:
            data = sock.recv(1024)
            packet_owner, entries = parse_rip_pkt(data)
            #if len(entries) != 0:
            update_routing_table(packet_owner, entries)
            print_routing_table(packet_owner)
        last_regular_time_up = process_timers(last_regular_time_up)

def process_timers(last_regular_time_up):
    """Process timers after 1 second listening, timers are actually time stamps
       Periodic update timer: PERIODIC_UPDATE_TIMER applying a random number
       Entry timer: TIMEOUT or GARBAGE COLLECTION
    """
    current_time = time.perf_counter()
    # if it's periodic update time
    if current_time - last_regular_time_up > PERIODIC_UPDATE_TIMER + random.randint(-20,20)/10:
        print(f"{datetime.datetime.now()}  Sending routing table upon regular update...", end='\n\n')
        send_routing_table()
        last_regular_time_up = time.perf_counter()

    deleted_items = []
    # for all the entries, if there is a time up
    for router_id, (next_hop_id, metrics, timer) in ROUTING_TABLE.items():
        # if this time up is 180 seconds time out
        #print(timer[0], current_time - timer[1])
        if timer[0] == 1 and current_time - timer[1] > TIMEOUT:
            ROUTING_TABLE[router_id][1] = 16
            ROUTING_TABLE[router_id][-1] = [2, time.perf_counter()] # start garbage collection

        # if this time up is 120 seconds garbage collection
        elif timer[0] == 2 and current_time - timer[1] > GARBAGE_COLL_TIMER:
            deleted_items.append(router_id)

    for item in deleted_items:
        print(f"{datetime.datetime.now()}  Deleting router {item} from routing table (Garbage Collection)", end='\n\n')
        ROUTING_TABLE.pop(item)
    if len(deleted_items) > 0:
        print_routing_table(-1)
        print()
        print(f"{datetime.datetime.now()}  Sending routing table after deleting...", end='\n\n')
        send_routing_table()
    return last_regular_time_up



def update_routing_table(neighbor_router_id, entries):
    """Routing table: router_id, next_hop_id, metrics, timer"""
    if ROUTING_TABLE.get(neighbor_router_id) is None: # if the neighbor is not in routing table
        ROUTING_TABLE[neighbor_router_id] = [neighbor_router_id, LINKS[neighbor_router_id][1], [1, time.perf_counter()]]
    else:
        # refresh the neighbor router where the packet comes
        if LINKS[neighbor_router_id][1] < ROUTING_TABLE[neighbor_router_id][1]:
            ROUTING_TABLE[neighbor_router_id][0] = neighbor_router_id
            ROUTING_TABLE[neighbor_router_id][1] = LINKS[neighbor_router_id][1]
        ROUTING_TABLE[neighbor_router_id][2] = [1, time.perf_counter()]

    for (router_id_dest, metric) in entries: # for each entry in this incoming packet
        new_route_metric = metric + ROUTING_TABLE[neighbor_router_id][1]
        if router_id_dest == ROUTER_ID: # entry for this router itself
            continue

        elif ROUTING_TABLE.get(router_id_dest) is None: # entry not in routing table
            if new_route_metric < 16: # it's a new and desirable route
                ROUTING_TABLE[router_id_dest] = [neighbor_router_id, metric+ROUTING_TABLE[neighbor_router_id][1], [1, time.perf_counter()]]

        else: # entry already in routing table
            # the metric of this entry smaller than current entry
            if new_route_metric < ROUTING_TABLE[router_id_dest][1]:
                ROUTING_TABLE[router_id_dest][0] = neighbor_router_id
                ROUTING_TABLE[router_id_dest][1] = new_route_metric
                ROUTING_TABLE[router_id_dest][2] = [1, time.perf_counter()]

            # if the metric is the same or larger, and the old route passes this neighbor
            elif neighbor_router_id == ROUTING_TABLE[router_id_dest][0]:
                # Exactly same entry (and it's a valid entry, only refresh its timer)
                if new_route_metric == ROUTING_TABLE[router_id_dest][1] and ROUTING_TABLE[router_id_dest][1] < 16:
                    ROUTING_TABLE[router_id_dest][2] = [1, time.perf_counter()]
                # New metric becomes larger than the current entry's
                elif new_route_metric > ROUTING_TABLE[router_id_dest][1] and ROUTING_TABLE[router_id_dest][1] < 16:
                    ROUTING_TABLE[router_id_dest][1] = 16 if new_route_metric >= 16 else new_route_metric
                    ROUTING_TABLE[router_id_dest][2] = [1, time.perf_counter()]



def send_routing_table():
    """Use the first UDP input socket to send routing tables to its neighbors"""
    for peer_router_id, (port, _) in LINKS.items():
        pkt = get_rip_pkt(peer_router_id)
        INPUT_SOCKETS[0].sendto(pkt, ('127.0.0.1', port))


def print_config_info():
    """Print out config info if the config file is parsed successfully"""
    print("-----Parse config file successfully-----")
    print("Router ID:", ROUTER_ID)
    print("Input Ports:", INPUT_PORTS)
    print("Links: ")
    for peer_router_id, (peer_input_port, metric) in LINKS.items():
        print("To Router", peer_router_id, "on its Port", peer_input_port, "Metric:", metric)
    print('-'*40)


def print_routing_table(ptk_owner):
    """pkt_owner specifies after receving whose packet, this function is called.
       except:
       -1: after deleting an entry, print routing table
    """
    if len(ROUTING_TABLE.items()) == 0:
        print(f"{datetime.datetime.now()}  Empty routing table, keep listening....")
    else:
        if ptk_owner >= 0:
            print(f"{datetime.datetime.now()}  After Receiving from router {ptk_owner}:")
        elif ptk_owner == -1:
            print(f"{datetime.datetime.now()}  After deleting entry(s) from the routing table:")

        print("-"*70)
        print(f"This Router: {ROUTER_ID}")
        for entry in ROUTING_TABLE.items():
            if entry[1][-1][0] == 1: # not yet timeout
                timer_description = 'online'
                time_left = TIMEOUT - (time.perf_counter()-entry[1][-1][1])
            elif entry[1][-1][0] == 2:
                timer_description = 'timeout, waiting to be deleted'
                time_left = GARBAGE_COLL_TIMER - (time.perf_counter()-entry[1][-1][1])
            print(f'Dest: {entry[0]}, next hop: {entry[1][0]}, cost: {entry[1][1]},'\
                  +' status: {}, Time left: {:.3f}'.format(timer_description, time_left))
        print("-"*70, end='\n\n')



def main():
    """Main funtion"""
    parse_conf_file()
    bind_sockets()
    listening_loop()

main()
