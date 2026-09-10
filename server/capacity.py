#%%

rates = [
    [   'g',    6.0,    108.7],
    [   'g',    9.0,    95.3],
    [   'g',    12.0,   88.7],
    [   'g',    18.0,   82.0],
    [   'g',    24.0,   78.7],
    [   'g',    36.0,   75.3],
    [   'g',    48.0,   73.7],
    [   'g',    54.0,   73.1],
    [   'n',    6.5,    105.6],
    [   'n',    13.0,   87.1],
    [   'n',    19.5,   81.0],
    [   'n',    26.0,   77.9],
    [   'n',    39.0,   74.8],
    [   'n',    52.0,   73.3],
    [   'n',    58.5,   72.8],
    [   'n',    65.0,   72.4],
]


def packet_time(r, payload_bytes):
    ofdm_header_time        = 20.0
    mac_header_bytes        = 30
    llc_ipv4_udp_bytes      = 8 + 20 + 8
    sifs_time               = 10.0
    ack_frame_header_time   = 20.0
    ack_frame_bytes         = 14
    bit_time                = 1.0 / r[1]
    total_time = (
        ofdm_header_time +
        (   mac_header_bytes +
            llc_ipv4_udp_bytes +
            ack_frame_bytes +
            payload_bytes) * 8 * bit_time +
        sifs_time +
        ack_frame_header_time
    )
    return total_time




freq = 50.0
period = 1.0 / freq
s_packet = 512
c_packet = 256
min_orbs=1
max_orbs=51
# for i in rates:
#     str+=f'{i:6d}'

str=''
for r in rates:
    str += f'{r[1]:4.1f}{r[0]} '
print('    ' + str)
str=''
for r in rates:
    e_time = packet_time(r, 0)
    str += f'{e_time:5.0f} '
print('    ' + str)
str=''
for r in rates:
    s_time = packet_time(r, s_packet)
    str += f'{s_time:5.0f} '
print('    ' + str)
str=''
for r in rates:
    c_time = packet_time(r, c_packet)
    str += f'{c_time:5.0f} '
print('    ' + str)

for n in range(min_orbs, max_orbs):
    print(f'{n:3d} ', end='')
    for r in rates:
        s_time = packet_time(r, s_packet)
        c_time = packet_time(r, c_packet)
        t_time = ((s_time + c_time) * n) * 0.001
        print(f'{t_time:5.1f} ', end='')
    print()
    

#%%