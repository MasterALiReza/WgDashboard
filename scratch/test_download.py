import re

def downloadPeer(name):
    final = {"fileName": "", "file": ""}
    filename = name
    if not filename or len(filename) == 0:
        filename = "UntitledPeer"
    filename = "".join(filename.split(' '))
    filename = re.sub(r'[.,/?<>\\:*|"]', '', filename).rstrip(". ")
    reserved_pattern = r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?$"
    if re.match(reserved_pattern, filename, re.IGNORECASE):
        filename = f"file_{filename}"
    for i in filename:
        if re.match("^[a-zA-Z0-9_=+.-]$", i):
            final["fileName"] += i
    return final

print("Persian:", downloadPeer("وایرگارد کالاف"))
