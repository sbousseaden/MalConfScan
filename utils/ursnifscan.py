# Detecting Ursnif for Volatility
#
# LICENSE
# Please refer to the LICENSE.txt in the https://github.com/JPCERTCC/MalConfScan/
#
# How to use:
# 1. cd "Volatility Folder"
# 2. mv ursnifscan.py volatility/plugins/malware
# 3. python vol.py ursnifconfig -f images.mem --profile=Win7SP1x64

import volatility.plugins.taskmods as taskmods
import volatility.win32.tasks as tasks
import volatility.utils as utils
import volatility.debug as debug
import volatility.plugins.malware.malfind as malfind
import re
from struct import unpack, unpack_from
from collections import OrderedDict

try:
    import yara
    has_yara = True
except ImportError:
    has_yara = False

try:
    import aplib
    has_aplib = True
except ImportError:
    has_aplib = False

ursnif_sig = {
    'namespace1' : 'rule Ursnif { \
                    strings: \
                       $a1 = "soft=%u&version=%u&user=%08x%08x%08x%08x&server=%u&id=%u&crc=%x"\
                       $b1 = "client.dll" fullword\
                       $c1 = "version=%u"\
                       $c2 = "user=%08x%08x%08x%08x"\
                       $c3 = "server=%u"\
                       $c4 = "id=%u"\
                       $c5 = "crc=%u"\
                       $c6 = "guid=%08x%08x%08x%08x"\
                       $c7 = "name=%s"\
                       $c8 = "soft=%u"\
                    condition: $a1 or ($b1 and 3 of ($c*)) or (5 of ($c*))}'
}

# Magic pattern
magic = "J[1J]"

DT_STR = 1
idx_list = {
    0x0d20203c: ["lang_id", DT_STR],
    0x11271c7f: ["sleep_time", DT_STR],
    0x18a632bb: ["time_value", DT_STR],
    0x31277bd5: ["SetWaitableTimer_value(CRC_TASKTIMEOUT)", DT_STR],
    0x4b214f54: ["tor64_dll", DT_STR],
    0x4fa8693e: ["serpent_key", DT_STR],
    0x510f22d2: ["c2_tor_domain", DT_STR],
    0x556aed8f: ["server", DT_STR],
    0x584e5925: ["SetWaitableTimer_value", DT_STR],
    0x602c2c26: ["capture_window_title?(CRC_KEYLOGLIST)"],
    0x656b798a: ["botnet", DT_STR],
    0x6de85128: ["not_use(CRC_BCTIMEOUT)", DT_STR],
    0x73177345: ["dga_base_url", DT_STR],
    0x746ce763: ["movie_capture", DT_STR],
    0x75e6145c: ["c2_domain", DT_STR],
    0x758a4250: ["check_vm", DT_STR],
    0x955879a6: ["SetWaitableTimer_value(CRC_SENDTIMEOUT)", DT_STR],
    0x9fd13931: ["SOCKS_backconnect_server(CRC_BCSERVER)", DT_STR],
    0xacc79a02: ["SetWaitableTimer_value(CRC_KNOCKERTIMEOUT)", DT_STR],
    0xb892845a: ["tor_server3", DT_STR],
    0xc61efa7a: ["dga_tld", DT_STR],
    0xd0665bf6: ["c2_domain", DT_STR],
    0xd7a003c9: ["SetWaitableTimer_value(CRC_CONFIGTIMEOUT)", DT_STR],
    0xdf351e24: ["tor32_dll", DT_STR],
    0xefc574ae: ["dga_seed", DT_STR],
    0xec99df2e: ["ip_check_url", DT_STR],
    0xea9ea760: ["p2p_bootstrap", DT_STR],
}


class ursnifConfig(taskmods.DllList):
    """Parse the Ursnif configuration"""

    @staticmethod
    def is_valid_profile(profile):
        return (profile.metadata.get('os', 'unknown') == 'windows'), profile.metadata.get('memory_model', '32bit')

    def get_vad_base(self, task, address):
        for vad in task.VadRoot.traverse():
            if address >= vad.Start and address < vad.End:
                return vad.Start, vad.End

        return None

    def parse_config(self, data):
        p_data = OrderedDict()
        (count,) = unpack_from("<Q", data, 0x0)
        #print("[+] config > number of elements : %d" % count)
        for i in range(count):
            (idx, flags, off, uid) = unpack_from("<LLQQ", data, 8 + i * 24)
            field = "unknown"
            off += 8 + i * 24
            if idx in idx_list:
                field = idx_list[idx][0]
            else:
                field = hex(idx)
            p_data[field] = data[off:].split("\x00")[0]

        return p_data

    def calculate(self):

        if not has_yara:
            debug.error("Yara must be installed for this plugin")

        if not has_yara:
            debug.error("Aplib must be installed for this plugin")

        addr_space = utils.load_as(self._config)

        os, memory_model = self.is_valid_profile(addr_space.profile)
        if not os:
            debug.error("This command does not support the selected profile.")

        rules = yara.compile(sources=ursnif_sig)

        for task in self.filter_tasks(tasks.pslist(addr_space)):
            scanner = malfind.VadYaraScanner(task=task, rules=rules)

            for hit, address in scanner.scan():

                vad_base_addr, end = self.get_vad_base(task, address)
                proc_addr_space = task.get_process_address_space()
                data = proc_addr_space.zread(vad_base_addr, end - vad_base_addr)

                config_data = []

                mz_magic = unpack_from("=2s", data, 0x0)[0]
                nt_magic = unpack_from("<H", data, 0x3c)[0]

                if mz_magic == "\x00\x00":
                    data = "\x4d\x5a" + data[2:]
                    data = data[:nt_magic] + "\x50\x45" + data[nt_magic + 2:]
                fnames = []
                for m in re.finditer(magic + "\x00.", data):
                    xor_dword = 0
                    magic_dword = data[m.start():m.start() + 4]
                    if (magic_dword[0:1] == "J1" or magic_dword[3] == "\0"):
                        (flags, crc32_name, addr, size) = unpack_from("<LLLL", data, m.start() + 4)
                        print("[+] magic: {0} flags: 0x{1:X} crc32_name: 0x{2:X} addr: 0x{3:X} size: 0x{4:X}\n".format(
                            repr(magic_dword), flags, crc32_name, addr, size))
                    elif (magic_dword[0:1] == "JJ" or (ord(magic_dword[3]) & 1) == 1):
                        (xor_dword, crc32_name, addr, size) = unpack_from("<LLLL", data, m.start() + 4)
                        print("[+] magic: {0} xor: 0x{1:X} crc32_name: 0x{2:X} addr: 0x{3:X} size: 0x{4:X}\n".format(
                            repr(magic_dword), xor_dword, crc32_name, addr, size))
                    else:
                        raise ValueError("Unknown joiner header")

                    if size > 0x80000:
                        print("[!] size is too large, skipped this entry\n")
                        continue

                    try:
                        offset = addr
                    except:
                        print("[!] This PE is old Ursnif (not DreamBot)\n")
                        (addr, size, crc32_name, flags) = unpack_from(
                            "<LLLL", data, m.start() + 4)
                        print("[+] magic: {0} addr: 0x{1:X} size: 0x{2:X} crc32_name: 0x{3:X} flags: 0x{4:X}\n".format(
                            repr(magic_dword), addr, size, crc32_name, flags))
                        offset = addr
                    joined_res = data[offset:offset + size]
                    try:
                        dec_data = aplib.decompress(joined_res).do()[0]
                    except:
                        print("[!] Cann't decode data.\n")
                        continue

                    if (xor_dword != 0):
                        mod_data = ""
                        for i in range(min(4, size + 1)):
                            mod_data += chr(ord(dec_data[i]) ^ ((xor_dword >> 8 * i) & 0xff))
                        if (size >= 4):
                            mod_data += dec_data[4:]
                        dec_data = mod_data

                    if crc32_name in (0x4f75cea7, 0x9e154a0c):
                        fname = "ursnif_client32.bin"
                        open(fname, "wb").write(dec_data)
                        print("[+] dumped 32 bit client dll: {0}\n".format(fname))
                        fnames.append(fname)
                    elif crc32_name in (0x90f8aab4, 0x41982e1f):
                        fname = "ursnif_client64.bin"
                        open(fname, "wb").write(dec_data)
                        print("[+] dumped 64 bit client dll: {0}\n".format(fname))
                        # fnames.append(fname)

                    elif crc32_name in (0xe1285e64,):
                        fname = "ursnif_public_key.bin"
                        open(fname, "wb").write(dec_data)
                        print("[+] dumped public key: {0}\n".format(fname))
                    elif crc32_name in (0xd722afcb, 0x8365b957, 0x8fb1dde1):
                        fname = "ursnif_st_config.bin"
                        open(fname, "wb").write(dec_data)
                        print("[+] dumped static config: {0}\n".format(fname))
                        config_data.append(self.parse_config(dec_data))
                    else:
                        fname = "ursnif_" + hex(addr) + "_ap32_dec.bin"
                        open(fname, "wb").write(dec_data)
                        print("[+] dumped: {0}".format(fname))
                for fname in fnames:
                    parse_joinned_data(fname, magic)

                yield task, vad_base_addr, end, hit, memory_model, config_data
                break

    def render_text(self, outfd, data):

        delim = '-' * 70

        for task, start, end, malname, memory_model, config_data in data:
            outfd.write("{0}\n".format(delim))
            outfd.write("Process: {0} ({1})\n\n".format(task.ImageFileName, task.UniqueProcessId))

            outfd.write("[Config Info]\n")
            for p_data in config_data:
                for id, param in p_data.items():
                    outfd.write("{0:<16}: {1}\n".format(id, param))
