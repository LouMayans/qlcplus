project(plugins)

# libusb-1.0
pkg_check_modules(LIBUSB_1 IMPORTED_TARGET libusb-1.0)

add_subdirectory(artnet)
add_subdirectory(E1.31)
add_subdirectory(loopback)
add_subdirectory(osc)

if(NOT ANDROID AND NOT IOS)
    pkg_check_modules(LIBOLA IMPORTED_TARGET libola)
    pkg_check_modules(LIBOLASERVER IMPORTED_TARGET libolaserver)

    add_subdirectory(dmxusb)
    add_subdirectory(peperoni)
    add_subdirectory(udmx)
    add_subdirectory(midi)
    #add_subdirectory(velleman)
    add_subdirectory(enttecwing)
    add_subdirectory(hid)
    add_subdirectory(os2l)
if(UNIX AND NOT APPLE)
    add_subdirectory(dmx4linux)
endif()
if (NOT WIN32 AND NOT APPLE)
    add_subdirectory(spi)
endif()
if(UNIX AND ${LIBOLA_FOUND} AND ${LIBOLASERVER_FOUND})
    add_subdirectory(ola)
endif()

#    add_subdirectory(uart)
#    add_subdirectory(gpio)
endif()
