project(webfiles)

# Set the files for the webfiles target
set(WEBFILES_COMMON
    common.css
    keypad.html
    networkconfig.js
    configuration.js
    websocket.js
    favicon.ico
    favicon-192x192.png
)

set(WEBFILES_QML
    simpledesk-v5.css
    simpledesk-v5.js
    webaccess-v5.css
    webaccess-v5.js
    webaccess-v5.html
)

set(WEBFILES_CLASSIC
    simpledesk.css
    simpledesk.js
    virtualconsole.css
    virtualconsole.js
    control.html
    stage.html
    stage.css
    stage-settings.css
    stage-app.js
    stage-ws.js
    stage-units.js
    stage-rig.js
    stage-scene.js
    stage-editor.js
    stage-camera.js
    stage-props.js
    stage-textures.js
    stage-propeditor.js
    stage-propeditor.css
    stage-truss.js
    stage-looks.js
    stage-beams.js
    stage-render.js
    stage-ledstrip.js
)

if(qmlui)
    set(WEBFILES ${WEBFILES_COMMON} ${WEBFILES_QML})
else()
    set(WEBFILES ${WEBFILES_COMMON} ${WEBFILES_CLASSIC})
endif()

# Set the path for the webfiles target
set(WEBFILES_PATH "${INSTALLROOT}/${WEBFILESDIR}")

# Install the webfiles target to the specified path
install(FILES ${WEBFILES} DESTINATION ${WEBFILES_PATH})

# 3D stage view: vendored three.js (keeps its folder layout for relative imports)
# and QLC+'s generic fixture meshes as built-in looks
if(NOT qmlui)
    install(DIRECTORY three DESTINATION ${WEBFILES_PATH})
    file(GLOB STAGE_BUILTIN_MESHES ${CMAKE_CURRENT_SOURCE_DIR}/../../resources/meshes/fixtures/*.dae)
    install(FILES ${STAGE_BUILTIN_MESHES} DESTINATION ${WEBFILES_PATH}/stage-lib/builtin)
endif()
