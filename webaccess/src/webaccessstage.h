/*
  Q Light Controller Plus
  webaccessstage.h

  Copyright (c) Massimo Callegari

  Licensed under the Apache License, Version 2.0 (the "License");
  you may not use this file except in compliance with the License.
  You may obtain a copy of the License at

      http://www.apache.org/licenses/LICENSE-2.0.txt

  Unless required by applicable law or agreed to in writing, software
  distributed under the License is distributed on an "AS IS" BASIS,
  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
  See the License for the specific language governing permissions and
  limitations under the License.
*/

#ifndef WEBACCESSSTAGE_H
#define WEBACCESSSTAGE_H

#include <QObject>
#include <QList>
#include <QString>
#include <QByteArray>

class QHttpConnection;
class QTimer;
class Doc;

/**
 * Server side of the 3D stage view (/stage): the live rig description,
 * the stage and prop files, and the DMX stream pushed to subscribed pages.
 * Protocol: .claude/memory/stage-visualizer.md
 */
class WebAccessStage final : public QObject
{
    Q_OBJECT
public:
    explicit WebAccessStage(Doc *doc, QObject *parent = nullptr);

    /** The patched fixtures with their definition data, as JSON */
    QString rigJson(const QString &showPath) const;

    /** {path, exists, rev, stage} for the stage file of the given show */
    QString stageJson(const QString &showPath) const;
    /** Validate and write the stage file. Returns "OK|rev" or "ERR|reason" */
    QString saveStage(const QString &showPath, const QString &json);

    /** {path, exists, rev, props} for the user's prop library */
    QString propsJson() const;
    /** Validate and write the prop library. Returns "OK|rev" or "ERR|reason" */
    QString saveProps(const QString &json);

    int stageRev() const { return m_stageRev; }
    int propsRev() const { return m_propsRev; }

    void subscribe(QHttpConnection *conn);
    void unsubscribe(QHttpConnection *conn);
    /** Send a message to every subscribed page */
    void relay(const QString &message) const;

    static QString stageFilePath(const QString &showPath);
    static QString propsFilePath();

    /** MIME type for a /stage-lib or /gobos file, empty if not served */
    static QString mimeType(const QString &fileName);
    /** root + relative path, or empty if it would leave root */
    static QString safeJoin(const QString &root, const QString &relPath);

signals:
    /** A message for every connected WebSocket client */
    void broadcast(const QString &message);

private slots:
    void slotRigChanged();
    void slotRigTimeout();
    void slotDmxTick();

private:
    QList<QByteArray> readUniverses() const;
    QString readJsonFile(const QString &path, const QString &key, int rev) const;
    QString writeJsonFile(const QString &path, const QString &json, int &rev);

private:
    Doc *m_doc;
    QList<QHttpConnection *> m_subscribers;
    QList<QByteArray> m_lastFrames;
    QTimer *m_dmxTimer;
    QTimer *m_rigTimer;
    quint32 m_serial;
    int m_stageRev;
    int m_propsRev;
};

#endif // WEBACCESSSTAGE_H
