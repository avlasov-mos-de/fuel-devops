from ipaddr import IPNetwork
from devops.driver.libvirt.libvirt_driver import LibvirtDriver

from django.db import models

def choices(*args, **kwargs):
    defaults = {'max_length':255, 'null':False}
    defaults.update(kwargs)
    defaults.update(choices=double_tuple(*args))
    return models.CharField(**defaults)

def double_tuple(*args):
    dict = []
    for arg in args:
        dict.append((arg,arg))
    return tuple(dict)

_driver = None
def get_driver():
    """
        :rtype : LibvirtDriver
    """
    global _driver
    return _driver or LibvirtDriver()


class ExternalModel(models.Model):

    @property
    def driver(self):
        """
        :rtype : LibvirtDriver
        """
        return get_driver()

    name = models.CharField(max_length=255, unique=False, null=False)
    uuid = models.CharField(max_length=255)

    class Meta:
        unique_together = ('name', 'environment')

class Environment(models.Model):
    name = models.CharField(max_length=255, unique=True, null=False)

#   TODO find corresponded place
    @property
    def driver(self):
        """
        :rtype : LibvirtDriver
        """
        return get_driver()

    @property
    def volumes(self):
        return Volume.objects.filter(environment=self)

    @property
    def networks(self):
        return Network.objects.filter(environment=self)

    @property
    def nodes(self):
        return Node.objects.filter(environment=self)

    def allocated_networks(self):
        return self.driver.get_allocated_networks()

    def allocate_network(self, pool):
        while True:
            ip_network = pool.next()
            if not Network.objects.filter(ip_network=str(ip_network)).count():
                return ip_network

    def node_by_name(self, name):
        return self.nodes.filter(name=name, environment=self)

    def nodes_by_role(self, role):
        return self.nodes.filter(role=role, environment=self)

    def network_by_name(self, name):
        return self.networks.filter(name=name, environment=self)

    def define(self):
        for network in self.networks:
            network.define()
        for volume in self.volumes:
            volume.define()
        for node in self.nodes:
            node.define()

    def start(self):
        for network in self.networks:
            network.start()
        for node in self.nodes:
            node.start()

    def destroy(self):
        for node in self.nodes:
            node.destroy()

    def remove(self):
        for node in self.nodes:
            node.remove()
        for network in self.networks:
            network.remove()
        for volume in self.volumes:
            volume.remove()


class Network(ExternalModel):
    _iterhosts = None

    has_dhcp_server = models.BooleanField()
    has_pxe_server = models.BooleanField()
    has_reserved_ips = models.BooleanField(default=True)
    tftp_root_dir = models.CharField(max_length=255)
    forward = choices('nat', 'route', 'bridge', 'private', 'vepa', 'passthrough', 'hostdev')
    ip_network = models.CharField(max_length=255, unique=True)
    environment = models.ForeignKey(Environment, null=True)

    @property
    def interfaces(self):
        return Interface.objects.filter(network=self)

    @property
    def ip_pool_start(self):
        return IPNetwork(self.ip_network)[2]

    @property
    def ip_pool_end(self):
        return IPNetwork(self.ip_network)[-2]

    def next_ip(self):
        while True:
            self._iterhosts = self._iterhosts or IPNetwork(self.ip_network).iterhosts()
            ip = self._iterhosts.next()
            if ip<self.ip_pool_start or ip>self.ip_pool_end:
                continue
            if not Address.objects.filter(interface__network=self, ip_address=str(ip)).count():
                return ip

    def bridge_name(self):
        return self.driver.network_bridge_name(self)

    def define(self):
        self.driver.network_define(self)
        self.save()

    def start(self):
        self.create(verbose=False)

    def create(self, verbose = True):
        if verbose or not self.driver.network_active(self):
            self.driver.network_create(self)

    def destroy(self):
        self.driver.network_destroy(self)

    def erase(self):
        self.remove(verbose=False)

    def remove(self, verbose = True):
        if self.driver.network_exists(self) or verbose:
            if self.driver.network_active(self):
                self.driver.network_destroy(self)
            self.driver.network_undefine(self)
        self.delete()

class Node(ExternalModel):
    hypervisor = choices('kvm')
    os_type = choices('hvm')
    architecture = choices('x86_64','i686')
    boot = ['network', 'cdrom', 'hd']
    metadata = models.CharField(max_length=255, null=True)
    role = models.CharField(max_length=255, null=True)
    vcpu = models.PositiveSmallIntegerField(null=False, default=1)
    memory = models.IntegerField(null=False, default=1024)
    has_vnc = models.BooleanField(null=False, default=True)
    environment = models.ForeignKey(Environment, null=True)

    @property
    def disk_devices(self):
        return self.disk_devices.filter(node=self)

    @property
    def interfaces(self):
        return Interface.objects.filter(node=self)

    @property
    def disk_devices(self):
        return DiskDevice.objects.filter(node=self)

    @property
    def networks(self):
        return Network.objects.filter(interface__node=self)

    def interface_by_name(self, name):
        self.interfaces.filter(name=name)

    def define(self):
        self.driver.node_define(self)
        self.save()

    def start(self):
        self.create(verbose=False)

    def create(self, verbose=True):
        if verbose or not self.driver.node_active(self):
            self.driver.node_create(self)

    def destroy(self):
        self.driver.node_destroy(self)

    def erase(self):
        self.remove(verbose=False)

    def remove(self, verbose = True):
        if verbose or self.driver.node_exists(self):
            if self.driver.node_active(self):
                self.driver.node_destroy(self)
            self.driver.node_undefine(self)
        self.delete()

class DiskDevice(models.Model):
    device = choices('disk', 'cdrom')
    type = choices('file')
    bus = choices('virtio')
    target_dev =  models.CharField(max_length=255, null=False)
    node = models.ForeignKey(Node, null=True)

class Volume(ExternalModel):
    capacity = models.IntegerField(null=False)
    backing_store = models.ForeignKey('self', null=True)
    format = models.CharField(max_length=255, null=False)
    environment = models.ForeignKey(Environment, null=True)

    def define(self):
        self.driver.volume_define(self)
        self.save()

    def remove(self):
        self.driver.volume_delete(self)
        self.delete()

class Interface(models.Model):
    mac_address = models.CharField(max_length=255, unique=True, null=False)
    network = models.ForeignKey(Network)
    node = models.ForeignKey(Node)
    type = models.CharField(max_length=255, null=False)
    target_dev = models.CharField(max_length=255, unique=True, null=True)

    @property
    def addresses(self):
        return Address.objects.filter(interface=self)

    def add_address(self, address):
        Address.objects.create(ip_address=address, interface=self)

class Address(models.Model):
    ip_address = models.GenericIPAddressField()
    interface = models.ForeignKey(Interface)