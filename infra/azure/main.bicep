// The TxLens demo VM and its network, deployed into one resource group by `make cloud-deploy`.
// Declarative: running it again with the same parameters changes nothing; a new SSH source
// address only updates the NSG rule. Host setup (Docker, firewall, updates) is done over SSH
// by remote/host-setup.sh, so it can be re-run without recreating the VM.

@description('Azure region, e.g. eastus (must be allowed by the subscription policy).')
param location string = resourceGroup().location

@description('VM size: the cheapest one with 4 GB RAM that the subscription allows.')
param vmSize string = 'Standard_B2als_v2'

@description('Linux admin user (SSH key login only).')
param adminUsername string = 'txlens'

@description('OpenSSH public key of the deploying Mac (~/.ssh/txlens_azure.pub).')
param sshPublicKey string

@description('The only address allowed to reach SSH, e.g. 203.0.113.7 (the deployer\'s IP).')
param sshSourceIp string

@description('DNS label: <label>.<region>.cloudapp.azure.com.')
param dnsLabel string

@description('Daily auto-shutdown time, HHmm.')
param shutdownTime string = '2300'

@description('Windows time zone id of the shutdown time.')
param shutdownTimeZone string = 'Central Standard Time'

@description('OS disk size in GB (Standard SSD).')
param osDiskSizeGB int = 64

@description('Tags put on every resource.')
param tags object

var name = 'txlens-demo'

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${name}-nsg'
  location: location
  tags: tags
  properties: {
    // Everything else inbound hits Azure's default DenyAllInBound rule.
    securityRules: [
      {
        name: 'ssh-from-owner'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: '${sshSourceIp}/32'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '22'
        }
      }
      {
        name: 'http-for-redirect-and-acme'
        properties: {
          priority: 110
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'Internet'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '80'
        }
      }
      {
        name: 'https'
        properties: {
          priority: 120
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'Internet'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '443'
        }
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: '${name}-vnet'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: ['10.42.0.0/16'] }
    subnets: [
      {
        name: 'default'
        properties: {
          addressPrefix: '10.42.0.0/24'
          networkSecurityGroup: { id: nsg.id }
        }
      }
    ]
  }
}

// Standard SKU (Basic is retired): static, and billed hourly even while the VM is deallocated.
resource publicIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: '${name}-ip'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: {
    publicIPAllocationMethod: 'Static'
    dnsSettings: { domainNameLabel: dnsLabel }
  }
}

resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: '${name}-nic'
  location: location
  tags: tags
  properties: {
    ipConfigurations: [
      {
        name: 'primary'
        properties: {
          subnet: { id: vnet.properties.subnets[0].id }
          privateIPAllocationMethod: 'Dynamic'
          publicIPAddress: { id: publicIp.id }
        }
      }
    ]
  }
}

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: '${name}-vm'
  location: location
  tags: tags
  properties: {
    hardwareProfile: { vmSize: vmSize }
    storageProfile: {
      imageReference: {
        publisher: 'Canonical'
        offer: 'ubuntu-24_04-lts'
        sku: 'server'
        version: 'latest'
      }
      osDisk: {
        name: '${name}-osdisk'
        createOption: 'FromImage'
        diskSizeGB: osDiskSizeGB
        deleteOption: 'Delete'
        managedDisk: { storageAccountType: 'StandardSSD_LRS' }
      }
    }
    osProfile: {
      computerName: name
      adminUsername: adminUsername
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            { path: '/home/${adminUsername}/.ssh/authorized_keys', keyData: sshPublicKey }
          ]
        }
        // unattended-upgrades on the VM installs security updates; Azure does not reboot it.
        patchSettings: { patchMode: 'ImageDefault' }
      }
    }
    // Trusted Launch (Secure Boot + vTPM) costs nothing extra.
    securityProfile: {
      securityType: 'TrustedLaunch'
      uefiSettings: { secureBootEnabled: true, vTpmEnabled: true }
    }
    networkProfile: {
      networkInterfaces: [{ id: nic.id, properties: { deleteOption: 'Delete' } }]
    }
    // Serial console and boot screenshots, for when SSH does not answer.
    diagnosticsProfile: { bootDiagnostics: { enabled: true } }
  }
}

// Nightly shutdown (deallocates the VM) in case `make cloud-stop` was forgotten.
resource autoShutdown 'Microsoft.DevTestLab/schedules@2018-09-15' = {
  name: 'shutdown-computevm-${vm.name}'
  location: location
  tags: tags
  properties: {
    status: 'Enabled'
    taskType: 'ComputeVmShutdownTask'
    dailyRecurrence: { time: shutdownTime }
    timeZoneId: shutdownTimeZone
    targetResourceId: vm.id
    notificationSettings: { status: 'Disabled' }
  }
}

output fqdn string = publicIp.properties.dnsSettings.fqdn
output vmName string = vm.name
output nsgName string = nsg.name
