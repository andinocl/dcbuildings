# Convert DC building footprints and addresses into importable OSM files.
from fiona import collection
from lxml import etree
from lxml.etree import tostring
from rtree import index
from shapely.geometry import asShape
from shapely import speedups
from sys import argv
from glob import glob
import re

speedups.enable()

# Converts given building and address shapefiles into corresponding OSM XML
# files.
def convert(buildingIn, addressIn, buildingOut, addressOut):
    # Load all addresses.
    addresses = []
    with collection(addressIn, "r") as input:
        for address in input:
            shape = asShape(address['geometry'])
            shape.original = address
            addresses.append(shape)

    # Load and index all buildings.
    buildingIdx = index.Index()
    buildings = []
    voids = []
    with collection(buildingIn, "r") as input:
        for building in input:
            building['shape'] = asShape(building['geometry'])
            if building['properties']['DESCRIPTIO'] == 'Void':
                voids.append(building)
            else:
                building['voids'] = []
                building['properties']['addresses'] = []
                buildings.append(building)
                buildingIdx.add(len(buildings) - 1, building['shape'].bounds)

    # Map addresses to buildings.
    for address in addresses:
        for i in buildingIdx.intersection(address.bounds):
            if buildings[i]['shape'].contains(address):
                buildings[i]['properties']['addresses'].append(
                    address.original)

    # Map voids to buildings.
    for void in voids:
        for i in buildingIdx.intersection(void['shape'].bounds):
            if buildings[i]['shape'].intersects(void['shape']):
                buildings[i]['voids'].append(void)

    # Generates a new osm id.
    osmIds = dict(node = -1, way = -1, rel = -1)
    def newOsmId(type):
        osmIds[type] = osmIds[type] - 1
        return osmIds[type]

    # Converts an address
    def convertAddress(address):
        result = dict()
        if all (k in address for k in ('ADDRNUM', 'STNAME', 'STREET_TYP', 'QUADRANT')):
            result['addr:housenumber'] = str(address['ADDRNUM'])
            if address['ADDRNUMSUF']:
                result['addr:housenumber'] = "%s %s" % \
                    (result['addr:housenumber'], address['ADDRNUMSUF'].title())
            if re.match('^(\d+)\w\w$', address['STNAME']): # Test for 2ND, 14TH, 21ST
                streetname = address['STNAME'].lower()
            else:
                streetname = address['STNAME'].title()
            result['addr:street'] = "%s %s %s" % \
                (streetname,
                address['STREET_TYP'].title(),
                address['QUADRANT'])
            result['addr:postcode'] = str(int(address['ZIPCODE']))
        return result

    # Appends an address to a given node or way.
    def appendAddress(address, element):
        for k, v in convertAddress(address['properties']).iteritems():
            element.append(etree.Element('tag', k=k, v=v))

    # Appends a building to a given OSM xml document.
    def appendBuilding(building, address, osmXml):
        def appendNewWay(coords, osmXml):
            way = etree.Element('way', visible='true', id=str(newOsmId('way')))
            firstNid = 0
            for i, coord in enumerate(coords):
                if i == 0: continue # the first and last coordinate are the same
                nid = str(newOsmId('node'))
                if i == 1: firstNid = nid
                node = etree.Element('node', visible='true', id=nid)
                node.attrib['lon'] = str(coord[0])
                node.attrib['lat'] = str(coord[1])
                osmXml.append(node)
                way.append(etree.Element('nd', ref=nid))
            way.append(etree.Element('nd', ref=firstNid)) # close way
            osmXml.append(way)
            return way

        # Export building, create multipolygon if there are void shapes.
        way = appendNewWay(building['geometry']['coordinates'][0], osmXml)
        voidWays = []
        for void in building['voids']:
            voidWays.append(appendNewWay(void['geometry']['coordinates'][0], osmXml))
        if len(voidWays) > 0:
            relation = etree.Element('relation', visible='true', id=str(newOsmId('way')))
            relation.append(etree.Element('member', type='way', role='outer', ref=way.get('id')))
            for voidWay in voidWays:
                relation.append(etree.Element('member', type='way', role='inner', ref=voidWay.get('id')))
            relation.append(etree.Element('tag', k='type', v='multipolygon'))
            osmXml.append(relation)
            way = relation
        way.append(etree.Element('tag', k='building', v='yes'))
        if address: appendAddress(address, way)

    # Export buildings. Only export address with building if thre is exactly
    # one address per building.
    addresses = []
    osmXml = etree.Element('osm', version='0.6', generator='alex@mapbox.com')
    for building in buildings:
        address = None
        if len(building['properties']['addresses']) == 1:
            address = building['properties']['addresses'][0]
        else:
            addresses.extend(building['properties']['addresses'])
        appendBuilding(building, address, osmXml)
    with open(buildingOut, 'w') as outFile:
        outFile.writelines(tostring(osmXml, pretty_print=True, xml_declaration=True, encoding='UTF-8'))
        print "Exported " + buildingOut

    # Export separate addresses.
    if (len(addresses) > 0):
        osmXml = etree.Element('osm', version='0.6', generator='alex@mapbox.com')
        for address in addresses:
            node = etree.Element('node', visible = 'true', id = str(newOsmId('node')))
            node.attrib['lon'] = str(address['geometry']['coordinates'][0])
            node.attrib['lat'] = str(address['geometry']['coordinates'][1])
            appendAddress(address, node)
            osmXml.append(node)
        with open(addressOut, 'w') as outFile:
            outFile.writelines(tostring(osmXml, pretty_print=True, xml_declaration=True, encoding='UTF-8'))
            print "Exported " + addressOut

# Run conversions. Expects an chunks/addresses-[tract id].shp for each
# chunks/buildings-[tract id].shp. Optinally convert only one census tract.
if (len(argv) == 2):
    convert(
        'chunks/buildings-%s.shp' % argv[1],
        'chunks/addresses-%s.shp' % argv[1],
        'osm/buildings-%s.osm' % argv[1],
        'osm/addresses-%s.osm' % argv[1])
else:
    buildingFiles = glob("chunks/buildings-*.shp")
    for buildingFile in buildingFiles:
        matches = re.match('^.*-(\d+)\.shp$', buildingFile).groups(0)
        convert(
            buildingFile,
            'chunks/addresses-%s.shp' % matches[0],
            'osm/buildings-%s.osm' % matches[0],
            'osm/addresses-%s.osm' % matches[0])
