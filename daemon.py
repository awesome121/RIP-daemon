#!/bin/python3

import sys, signal, socket, select, threading, time

ROUTING_TABLE = {} # a diactionary of router_id: [next_hop_id, metrics, timer]
INPUT_PORTS = []
INPUT_SOCKETS = []
LINKS = {} # router_id : (port, distance)
ROUTER_ID = 0
PERIODIC_UPDATE_TIMER = 30 # 30 seconds
TIMEOUT = 50 # 180 seconds
GARBAGE_COLL_TIMER = 70 # 120 seconds


def parse_conf_file():
    """Read sys.argv[1], raise exception if the file cannot be read or is corrupted
    """
    global INPUT_PORTS, OUTPUT_ADDRESS, ROUTER_ID
    try:
        f = open(sys.argv[1])
        lines = f.readlines()
    except:
        print("Cannot read the configuration file")
        raise
    
    router_info = []
    # this section is get the useful configuration information
    for line in lines:
        if line[0] == '#' or line[0] == '\n':
            continue
        else:
            router_info.append(line)
    
    # try to get the router ID from configuration file
    try:
        router_id = ''
        for char in router_info[0].strip("router-id ").rstrip("\n"):
            if char == '#':
                break
            else:
                router_id += char
            ROUTER_ID = int(router_id)
    except:
        print("router_id error")
        raise
    # try to get the input port from configuration file
    try:
        input_ports = ''
        for char in router_info[1].strip("input-ports ").rstrip("\n"):
            if char == '#':
                break
            else:
                input_ports += char
        input_ports = input_ports.split(',')
        
        for i in range(len(input_ports)):
            input_ports[i] = int(input_ports[i])
        INPUT_PORTS = input_ports
            
    except:
        print("input_ports error")
        raise
    
    # try to get the output port from configuration file
    try:
        outputs = ''
        for char in router_info[2].strip("outputs ").rstrip("\n"):
            if char == '#':
                break
            else:
                outputs += char
        outputs = outputs.split(',')
        for link in outputs:
            #split the outpot port to tuple and add to LINKS
            peer_input_port, metric, peer_router_id = [int(num) for num in link.split('-')]
            LINKS[peer_router_id] =  (peer_input_port, metric)
    except:
        print("outputs error")    
        raise


def get_rip_pkt(router_id_peer):
    """this function is for generate the response message
       entry: [dest_router_id, next_hop_id, metrics, timer]
    """

    pkt = bytearray([2, 2, 0, ROUTER_ID]) # set the package header with local router ID
    # combine the all entries in to the one packet
    for router_id_dest, (next_hop_id, metrics, _) in ROUTING_TABLE.items():  # use loop put all entries in the arrayList 
        if next_hop_id == router_id_peer: 
            metrics = 16 # add poison
        pkt += bytearray([0]*4 + [0, 0, 0, router_id_dest] + [0] * 8 + [0, 0, 0, metrics])

    return pkt


def parse_rip_pkt(received):
    """This function is parsing the received pkt and return a tuple about the router entries for local router use"""
    entry = []
    received = list(received)
    if received[0:3] != [2,2,0]:  #check the header is correct otherwise raise error
        raise 'the packet header error'
    
    else:
        #print('Receive from ', received[3])
        next_hop = received[3]  #parsing the packet from which hop and store it
        received = received[4:]  #remove the pkt header and get the left entity
        for i in range(0,len(received),20): #parsing the all entity, split by 20 byte per step
            entry.append((received[i+7], received[i+19]))  #each tuple contain router_id_dest, next hop and metric 
    return next_hop, entry


def bind_sockets():
    """bind the ip address with input port"""
    for input_port in INPUT_PORTS:
        new_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        new_socket.bind(('127.0.0.1', input_port))
        INPUT_SOCKETS.append(new_socket)


def listening_loop():
    """This function is for listening the packet for the network"""
    last_regular_time_up = time.perf_counter()
    send_routing_table()
    while True:       
        #print('Listening...')
        readable, writble, excep = select.select(INPUT_SOCKETS, [], [], 1)
        for sock in readable:
            data = sock.recv(1024)
            packet_owner, entries = parse_rip_pkt(data)
            update_routing_table(packet_owner, entries)
            print_routing_table()
        last_regular_time_up = process_timers(last_regular_time_up)
        
def process_timers(last_regular_time_up):
    current_time = time.perf_counter()
    # if it's regular update time
    if current_time - last_regular_time_up > PERIODIC_UPDATE_TIMER:
        print_routing_table()
        send_routing_table()
        last_regular_time_up = time.perf_counter()
    
    # for all the entries, if there is a time up
    for router_id, (next_hop_id, metrics, timer) in ROUTING_TABLE.items():
        # if this time up is 180 seconds time out
        #print(timer[0], current_time - timer[1])
        if timer[0] == 1 and current_time - timer[1] > TIMEOUT:
            print(1)
            ROUTING_TABLE[router_id][1] = 16
            ROUTING_TABLE[router_id][-1] = [2, timer.perf_counter()] # start garbage collection
          
        # if this time up is 120 seconds garbage collection  
        elif timer[0] == 2 and current_time - timer[1] > GARBAGE_COLL_TIMER:
            print(2)
            ROUTING_TABLE.pop(router_id)
            send_routing_table()   
    return last_regular_time_up
            
def print_routing_table():
    print("Routing table:")
    for entry in ROUTING_TABLE.items():
        print(f'Dest: {entry[0]}, next hop: {entry[1][0]}, cost: {entry[1][1]}, timer: {entry[1][-1]}')
    print() 

def update_routing_table(neighbor_router_id, entries):
    """routing table: router_id, next_hop_id, metrics, timer"""
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
        if router_id_dest == ROUTER_ID: # ignore this router's entry
            continue
            
        elif ROUTING_TABLE.get(router_id_dest) is None: # entry not in routing table
            if new_route_metric < 16: # it's a new and desirable route
                ROUTING_TABLE[router_id_dest] = [neighbor_router_id, metric+ROUTING_TABLE[neighbor_router_id][1], [1, time.perf_counter()]]
            else: # if the entry is not in the routing table and its metric is 16, ignore it
                continue
            
        else: # already in routing table
            # the metric of this entry smaller than current entry
            if new_route_metric < ROUTING_TABLE[router_id_dest][1]: # new_route_metric < 16 and
                #print(1, ROUTER_ID, neighbor_router_id, metric, router_id_dest)
                ROUTING_TABLE[router_id_dest][0] = neighbor_router_id
                ROUTING_TABLE[router_id_dest][1] = new_route_metric                  
                ROUTING_TABLE[router_id_dest][2] = [1, time.perf_counter()]
            
            # if the router does go through this neighbor where the packet comes (only update when it's valid)
            elif neighbor_router_id == ROUTING_TABLE[router_id_dest][0]:
                # exactly same entry (and it's a valid entry, only refresh its timer)
                if new_route_metric == ROUTING_TABLE[router_id_dest][1] and ROUTING_TABLE[router_id_dest][1] < 16:
                    #print(2, ROUTER_ID, neighbor_router_id, metric, router_id_dest)       
                    ROUTING_TABLE[router_id_dest][2] = [1, time.perf_counter()]
                # entry come from the same hop, but metric is larger than the current entry's
                elif new_route_metric > ROUTING_TABLE[router_id_dest][1] and ROUTING_TABLE[router_id_dest][1] < 16:
                    #print(3, ROUTER_ID, neighbor_router_id, metric, router_id_dest)
                    ROUTING_TABLE[router_id_dest][1] = 16 if new_route_metric >= 16 else new_route_metric              
                    ROUTING_TABLE[router_id_dest][2] = [1, time.perf_counter()]          
                
            
            
def send_routing_table():
    """This function is use the binded port and IP adderss to send routing table """
    for peer_router_id, (port, _) in LINKS.items():
        pkt = get_rip_pkt(peer_router_id)
        INPUT_SOCKETS[0].sendto(pkt, ('127.0.0.1', port))
                
#def entry_timer_handler(router_id_dest, flag): # 0 means 180 seconds, 1 means 120 seconds is up
    #print(f'Timeup: to {router_id_dest}', f'flag: {flag}')
    #if flag == 0: # time out
        
        #ROUTING_TABLE[router_id_dest][2] = time.perf_counter()
    #elif flag == 1: # garbage collection
        #ROUTING_TABLE.pop(router_id_dest)
        #send_routing_table()


#def set_regular_update_timer():
    #"""set the update timer every 30 second """
    #send_routing_table()
    #threading.Timer(UPDATE_TIMER, set_regular_update_timer).start()
    

def main():
    parse_conf_file()
    bind_sockets()
    listening_loop()



main()
